from __future__ import annotations

import re
from typing import Protocol

from .models import SearchIntent


class QueryParser(Protocol):
    def parse(self, text: str) -> SearchIntent: ...


class DemoQueryParser:
    """Deterministic Chinese parser used for demos and offline tests."""

    _locations = ("北京", "上海", "深圳", "广州", "杭州", "远程", "海外")
    _skills = ("Python", "LangGraph", "LangChain", "OpenAI", "RAG", "FastAPI", "Java", "C++")

    def parse(self, text: str) -> SearchIntent:
        raw = text.strip()
        locations = [item for item in self._locations if item in raw]
        skills = [item for item in self._skills if re.search(re.escape(item), raw, re.I)]
        excluded = re.findall(r"(?:排除|不要|不考虑)\s*([^，,。；;]+)", raw)
        excluded_roles = [
            part.strip()
            for value in excluded
            for part in re.split(r"、|或|/", value)
            if part.strip()
        ]
        role_keywords = []
        for role in ("Agent", "AI", "算法", "后端", "前端", "实习"):
            if re.search(re.escape(role), raw, re.I):
                role_keywords.append(role)
        required = skills if re.search(r"要求|必须|需要", raw) else []
        preferred = [] if required else skills
        return SearchIntent(
            raw_text=raw,
            role_keywords=role_keywords,
            locations=locations,
            required_skills=required,
            preferred_skills=preferred,
            excluded_roles=excluded_roles,
        )


class LlmQueryParser:
    def __init__(self, parser: QueryParser | None = None) -> None:
        self._parser = parser or DemoQueryParser()

    def parse(self, text: str) -> SearchIntent:
        return self._parser.parse(text)
