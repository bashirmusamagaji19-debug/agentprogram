from __future__ import annotations

import re

from web_task_agent.models import BrowserPage, JobPosting


class PageExtractor:
    _LABELS = {
        "title": {"title", "job title", "position"},
        "company": {"company", "employer"},
        "location": {"location", "city"},
        "requirements": {"requirements", "skills"},
        "responsibilities": {"responsibilities", "role"},
        "posted_at": {"posted", "posted at", "date"},
    }

    def extract(self, page: BrowserPage) -> JobPosting:
        fields = self._parse_labeled_lines(page.content)

        title = fields.get("title") or page.title
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
            bool(title.strip()),
            company.strip() != "Unknown Company",
            location.strip() != "Unknown Location",
            bool(requirements.strip()),
            bool(responsibilities.strip()),
        ]
        return sum(values) / len(values)
