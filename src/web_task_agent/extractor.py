from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from web_task_agent.models import BrowserPage, JobPosting


LlmFieldExtractor = Callable[[BrowserPage], dict[str, str]]


class PageExtractor:
    _LABELS = {
        "title": {"title", "job title", "position", "职位名称", "职位", "岗位名称", "岗位"},
        "company": {"company", "employer", "公司名称", "公司", "招聘单位"},
        "location": {"location", "city", "工作地点", "地点", "城市", "所在城市"},
        "requirements": {
            "requirements",
            "skills",
            "任职要求",
            "岗位要求",
            "任职资格",
            "要求",
        },
        "responsibilities": {
            "responsibilities",
            "role",
            "岗位职责",
            "工作职责",
            "职责描述",
            "职责",
            "工作内容",
        },
        "posted_at": {"posted", "posted at", "date", "发布时间", "发布日期"},
    }

    # 中文 JD 的标签行几乎全部使用全角冒号（"任职要求：…"），半角解析整条失效
    _FULLWIDTH_COLON = "："

    # 正文低于该字符数时不可能包含真实 JD，禁止触发 LLM 抽取——
    # 否则 LLM 会从"标题+部门+城市"这类标题行幻觉编造出整段任职要求（复现实录 #21）
    _MIN_CONTENT_FOR_LLM_CHARS = 100

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
        if (
            self.llm_field_extractor
            and job.confidence < self.llm_min_rule_confidence
            # 内容护栏：短于阈值的正文无 JD 信息可抽，调 LLM 只会得到幻觉
            and len(page.content.strip()) >= self._MIN_CONTENT_FOR_LLM_CHARS
        ):
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
                    "skills": llm_fields.get("skills", job.skills),
                },
            )
        return job

    def _job_from_fields(self, *, page: BrowserPage, fields: dict[str, Any]) -> JobPosting:
        title = fields.get("title") or page.title or "Unknown Title"
        company = fields.get("company") or "Unknown Company"
        location = fields.get("location") or "Unknown Location"
        requirements = fields.get("requirements", "")
        responsibilities = fields.get("responsibilities", "")
        # skills 优先 LLM 的结构化输出；LLM 未提供时保留规则切分兜底
        llm_skills = fields.get("skills")
        skills = (
            self._clean_skill_list(llm_skills)
            if isinstance(llm_skills, list) and llm_skills
            else self._extract_skills(requirements)
        )
        return JobPosting(
            title=title,
            company=company,
            location=location,
            source=page.source,
            url=page.url,
            requirements=requirements,
            responsibilities=responsibilities,
            skills=skills,
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
        lines = content.splitlines()

        index = 0
        while index < len(lines):
            line = lines[index]
            label, separator, value = self._split_label_line(line)
            field = label_map.get(label.strip().lower()) if separator else None
            if field is None:
                index += 1
                continue

            value = value.strip()
            if value:
                # "标签: 同行值" 式
                parsed[field] = value
                index += 1
                continue

            # 中文 JD 主流版式："岗位职责：" 为 section 标题行（值为空），
            # 内容在后续行，直到下一个标签行——逐行收集
            collected: list[str] = []
            cursor = index + 1
            while cursor < len(lines):
                next_label, next_sep, _ = self._split_label_line(lines[cursor])
                if next_sep and label_map.get(next_label.strip().lower()) is not None:
                    break
                stripped = lines[cursor].strip()
                if stripped:
                    collected.append(stripped)
                cursor += 1
            if collected:
                parsed[field] = " ".join(collected).strip()
            index = max(cursor, index + 1)

        return parsed

    def _split_label_line(self, line: str) -> tuple[str, str, str]:
        """按标签冒号切行：先半角':'，无则全角'：'。

        先半角的原因：半角冒号存在时内容里可能还有全角冒号，
        已切分正确就不再替换，避免破坏原行为。
        """
        label, separator, value = line.partition(":")
        if not separator:
            label, separator, value = line.partition(self._FULLWIDTH_COLON)
        return label, separator, value

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
            start_markers={
                "about the role",
                "responsibilities",
                "what you'll do",
                # 中文 JD section 标题
                "岗位职责",
                "工作职责",
                "工作内容",
                "职责描述",
                "职位描述",
            },
            stop_markers={
                "qualifications",
                "requirements",
                "skills",
                "posted",
                "任职要求",
                "岗位要求",
                "任职资格",
            },
        )
        if responsibilities:
            inferred["responsibilities"] = responsibilities

        requirements = self._infer_section(
            lines,
            start_markers={
                "qualifications",
                "requirements",
                "skills",
                "任职要求",
                "岗位要求",
                "任职资格",
            },
            stop_markers={
                "posted",
                "benefits",
                "about us",
                "发布时间",
                "福利待遇",
                "工作地址",
                "其他信息",
            },
        )
        if requirements:
            inferred["requirements"] = requirements

        return inferred

    def _infer_title(self, lines: list[str], page_title: str) -> str:
        first_line = lines[0]
        section_openers = {
            "about the role",
            "responsibilities",
            "requirements",
            "岗位职责",
            "工作职责",
            "任职要求",
        }
        if first_line.lower().rstrip(":：") in section_openers:
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
                company, location = company.strip(), location.strip()
                if self._looks_like_location(company):
                    # 整行是地点（中文官网式"上海市·浦东新区"）→ 公司名取上一行
                    if lines[0] and not self._looks_like_location(lines[0]):
                        return lines[0], company_line.strip()
                    return "", company_line.strip()
                return company, location
        if self._looks_like_location(company_line):
            # 纯地点行（"公司名\n城市" 两行式）
            if lines[0] and not self._looks_like_location(lines[0]):
                return lines[0], company_line.strip()
            return "", company_line.strip()
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
        location_tokens = {
            "remote",
            "shanghai",
            "beijing",
            "us",
            "china",
            "singapore",
            # 中文城市/地点（子串匹配，如"上海市"、“北京·海淀区"）
            "北京",
            "上海",
            "深圳",
            "广州",
            "杭州",
            "成都",
            "南京",
            "武汉",
            "西安",
            "苏州",
            "合肥",
            "长沙",
            "重庆",
            "天津",
            "远程",
            "全国",
        }
        lower_value = value.lower()
        return any(token in lower_value for token in location_tokens)

    def _has_labeled_lines(self, lines: list[str]) -> bool:
        known_labels = {
            label
            for labels in self._LABELS.values()
            for label in labels
        }
        for line in lines:
            label, separator, value = self._split_label_line(line)
            if separator and value.strip() and label.strip().lower() in known_labels:
                return True
        return False

    def _matches_marker(self, value: str, markers: set[str]) -> bool:
        # 中文 section 行常为"任职要求："/"任职要求"，剥掉尾部冒号后精确/前缀匹配
        stripped = value.rstrip(":：").strip()
        for marker in markers:
            if stripped == marker or value.startswith(f"{marker} "):
                return True
        return False

    def _extract_skills(self, requirements: str) -> list[str]:
        return [
            skill.strip()
            for skill in re.split(r"[,\uFF0C]", requirements)
            if skill.strip()
        ]

    def _clean_skill_list(self, skills: Any) -> list[str]:
        """LLM skills \u5217\u8868\u7684\u6700\u540E\u9632\u5FA1\uFF1A\u53EA\u7559\u975E\u7A7A\u5B57\u7B26\u4E32\uFF0C\u53BB\u91CD\u4FDD\u5E8F\u3002"""
        if not isinstance(skills, list):
            return []
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in skills:
            if not isinstance(item, str):
                continue
            skill = item.strip()
            key = skill.casefold()
            if skill and key not in seen:
                seen.add(key)
                cleaned.append(skill)
        return cleaned

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
