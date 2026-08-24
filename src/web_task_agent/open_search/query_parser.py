from __future__ import annotations

import re
from typing import Protocol

from .models import SearchIntent


class QueryParser(Protocol):
    def parse(self, text: str) -> SearchIntent: ...


class DemoQueryParser:
    """Deterministic Chinese parser used for demos and offline tests."""

    _locations = ("北京", "上海", "深圳", "广州", "杭州", "远程", "海外", "Remote", "Remote/Hybrid")
    _skills = ("Python", "LangGraph", "LangChain", "OpenAI", "RAG", "FastAPI", "Java", "C++")
    _chinese_counts = {
        "一": 1,
        "两": 2,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }

    def parse(self, text: str) -> SearchIntent:
        raw = text.strip()
        locations = [item for item in self._locations if item in raw]
        if re.search(r"\bremote\b", raw, re.I) and "Remote" not in locations:
            locations.append("Remote")
        skills = [item for item in self._skills if re.search(re.escape(item), raw, re.I)]
        excluded = re.findall(r"(?:排除|不要|不考虑)\s*([^，,。；;]+)", raw)
        excluded_roles = [
            part.strip()
            for value in excluded
            for part in re.split(r"、|或|/", value)
            if part.strip()
        ]
        role_keywords = []
        for role in (
            "Agent",
            "AI",
            "算法",
            "后端",
            "前端",
            "实习",
            "intern",
            "engineer",
            "developer",
        ):
            if re.search(re.escape(role), raw, re.I):
                role_keywords.append(role)
        required = skills if re.search(r"要求|必须|需要", raw) else []
        preferred = [] if required else skills
        count_match = re.search(
            r"(?:找|筛选|返回|需要|给我)?\s*(\d{1,2})\s*(?:个|条)?\s*(?:岗位|职位|结果|jobs?|results?)|"
            r"(?:top|前)\s*(\d{1,2})\b",
            raw,
            re.I,
        )
        target_count = 10
        if count_match:
            parsed_count = int(next(group for group in count_match.groups() if group))
            target_count = min(20, max(1, parsed_count))
        else:
            chinese_count = re.search(
                r"(?:找|筛选|返回|需要|给我)?\s*"
                r"([一二两三四五六七八九十])\s*(?:个|条)?\s*"
                r"(?:岗位|职位|结果)",
                raw,
            )
            if chinese_count:
                target_count = self._chinese_counts[chinese_count.group(1)]
        return SearchIntent(
            raw_text=raw,
            role_keywords=role_keywords,
            locations=locations,
            required_skills=required,
            preferred_skills=preferred,
            excluded_roles=excluded_roles,
            target_count=target_count,
        )


class LlmQueryParser:
    def __init__(self, parser: QueryParser | None = None) -> None:
        self._parser = parser or DemoQueryParser()

    def parse(self, text: str) -> SearchIntent:
        return self._parser.parse(text)
