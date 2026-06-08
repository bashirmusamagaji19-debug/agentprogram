from __future__ import annotations

import re
from collections.abc import Callable

from web_task_agent.models import BrowserPage, JobPosting


LlmFieldExtractor = Callable[[BrowserPage], dict[str, str]]


class PageExtractor:
    _LABELS = {
        "title": {"title", "job title", "position"},
        "company": {"company", "employer"},
        "location": {"location", "city"},
        "requirements": {"requirements", "skills"},
        "responsibilities": {"responsibilities", "role"},
        "posted_at": {"posted", "posted at", "date"},
    }

    def __init__(
        self,
        *,
        llm_field_extractor: LlmFieldExtractor | None = None,
        llm_min_rule_confidence: float = 0.6,
    ) -> None:
        self.llm_field_extractor = llm_field_extractor
        self.llm_min_rule_confidence = llm_min_rule_confidence

    def extract(self, page: BrowserPage) -> JobPosting:
        fields = self._parse_labeled_lines(page.content)
        inferred_fields = self._infer_public_job_fields(page)

        title = fields.get("title") or inferred_fields.get("title") or page.title or "Unknown Title"
        company = fields.get("company") or inferred_fields.get("company") or "Unknown Company"
        location = fields.get("location") or inferred_fields.get("location") or "Unknown Location"
        requirements = fields.get("requirements") or inferred_fields.get("requirements", "")
        responsibilities = fields.get("responsibilities") or inferred_fields.get("responsibilities", "")

        job = JobPosting(
            title=title,
            company=company,
            location=location,
            source=page.source,
            url=page.url,
            requirements=requirements,
            responsibilities=responsibilities,
            skills=self._extract_skills(requirements),
            posted_at=fields.get("posted_at", ""),
            confidence=self._confidence(
                title=title,
                company=company,
                location=location,
                requirements=requirements,
                responsibilities=responsibilities,
            ),
        )
        if self.llm_field_extractor and job.confidence < self.llm_min_rule_confidence:
            llm_fields = self.llm_field_extractor(page)
            return self._job_from_fields(
                page=page,
                fields={
                    "title": llm_fields.get("title", job.title),
                    "company": llm_fields.get("company", job.company),
                    "location": llm_fields.get("location", job.location),
                    "requirements": llm_fields.get("requirements", job.requirements),
                    "responsibilities": llm_fields.get(
                        "responsibilities",
                        job.responsibilities,
                    ),
                    "posted_at": llm_fields.get("posted_at", job.posted_at),
                },
            )
        return job

    def _job_from_fields(self, *, page: BrowserPage, fields: dict[str, str]) -> JobPosting:
        title = fields.get("title") or page.title or "Unknown Title"
        company = fields.get("company") or "Unknown Company"
        location = fields.get("location") or "Unknown Location"
        requirements = fields.get("requirements", "")
        responsibilities = fields.get("responsibilities", "")
        return JobPosting(
            title=title,
            company=company,
            location=location,
            source=page.source,
            url=page.url,
            requirements=requirements,
            responsibilities=responsibilities,
            skills=self._extract_skills(requirements),
            posted_at=fields.get("posted_at", ""),
            confidence=self._confidence(
                title=title,
                company=company,
                location=location,
                requirements=requirements,
                responsibilities=responsibilities,
            ),
        )

    def _parse_labeled_lines(self, content: str) -> dict[str, str]:
        parsed: dict[str, str] = {}
        label_map = {
            label: field
            for field, labels in self._LABELS.items()
            for label in labels
        }

        for line in content.splitlines():
            label, separator, value = line.partition(":")
            if not separator:
                continue

            field = label_map.get(label.strip().lower())
            value = value.strip()
            if field and value:
                parsed[field] = value

        return parsed

    def _infer_public_job_fields(self, page: BrowserPage) -> dict[str, str]:
        lines = [line.strip() for line in page.content.splitlines() if line.strip()]
        inferred: dict[str, str] = {}
        if not lines or self._has_labeled_lines(lines):
            return inferred

        inferred["title"] = self._infer_title(lines, page.title)
        company, location = self._infer_company_location(lines)
        if company:
            inferred["company"] = company
        if location:
            inferred["location"] = location

        responsibilities = self._infer_section(
            lines,
            start_markers={"about the role", "responsibilities", "what you'll do"},
            stop_markers={"qualifications", "requirements", "skills", "posted"},
        )
        if responsibilities:
            inferred["responsibilities"] = responsibilities

        requirements = self._infer_section(
            lines,
            start_markers={"qualifications", "requirements", "skills"},
            stop_markers={"posted", "benefits", "about us"},
        )
        if requirements:
            inferred["requirements"] = requirements

        return inferred

    def _infer_title(self, lines: list[str], page_title: str) -> str:
        first_line = lines[0]
        if first_line.lower() in {"about the role", "responsibilities", "requirements"}:
            return page_title
        if " - " in page_title and page_title.startswith(first_line):
            return first_line
        return page_title or first_line

    def _infer_company_location(self, lines: list[str]) -> tuple[str, str]:
        if len(lines) < 2:
            return "", ""

        company_line = lines[1]
        for separator in ("·", "|", " - "):
            if separator in company_line:
                company, location = company_line.split(separator, 1)
                return company.strip(), location.strip()
        if len(lines) >= 3 and self._looks_like_location(lines[2]):
            return company_line, lines[2]
        return company_line, ""

    def _infer_section(
        self,
        lines: list[str],
        *,
        start_markers: set[str],
        stop_markers: set[str],
    ) -> str:
        collecting = False
        collected: list[str] = []
        for line in lines:
            key = line.strip().lower()
            if collecting and self._matches_marker(key, stop_markers):
                break
            if self._matches_marker(key, start_markers):
                collecting = True
                continue
            if collecting:
                collected.append(line)
        return " ".join(collected).strip()

    def _looks_like_location(self, value: str) -> bool:
        location_tokens = {"remote", "shanghai", "beijing", "us", "china", "singapore"}
        lower_value = value.lower()
        return any(token in lower_value for token in location_tokens)

    def _has_labeled_lines(self, lines: list[str]) -> bool:
        known_labels = {
            label
            for labels in self._LABELS.values()
            for label in labels
        }
        for line in lines:
            label, separator, value = line.partition(":")
            if separator and value.strip() and label.strip().lower() in known_labels:
                return True
        return False

    def _matches_marker(self, value: str, markers: set[str]) -> bool:
        return any(value == marker or value.startswith(f"{marker} ") for marker in markers)

    def _extract_skills(self, requirements: str) -> list[str]:
        return [
            skill.strip()
            for skill in re.split(r"[,\uFF0C]", requirements)
            if skill.strip()
        ]

    def _confidence(
        self,
        *,
        title: str,
        company: str,
        location: str,
        requirements: str,
        responsibilities: str,
    ) -> float:
        values = [
            title.strip() != "Unknown Title",
            company.strip() != "Unknown Company",
            location.strip() != "Unknown Location",
            bool(requirements.strip()),
            bool(responsibilities.strip()),
        ]
        return sum(values) / len(values)
