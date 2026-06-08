from __future__ import annotations

import re

from web_task_agent.models import BrowserPage


class DemoLlmFieldExtractor:
    """Deterministic stand-in for an LLM structured extraction call."""

    def __call__(self, page: BrowserPage) -> dict[str, str]:
        content = " ".join(line.strip() for line in page.content.splitlines() if line.strip())
        return {
            "title": self._title(page, content),
            "company": self._company(content),
            "location": self._location(content),
            "requirements": self._requirements(content),
            "responsibilities": self._responsibilities(content),
        }

    def _title(self, page: BrowserPage, content: str) -> str:
        match = re.search(
            r"\b(?:hiring|role is for)\s+(?:an?\s+)?(.+?)(?:\s+at|\.)",
            content,
            re.I,
        )
        if match:
            return match.group(1).strip()
        return page.title

    def _company(self, content: str) -> str:
        match = re.search(r"\bat\s+([A-Z][A-Za-z0-9 ]+?)(?:\.|\s+This|\s+Candidates|$)", content)
        return match.group(1).strip() if match else "Unknown Company"

    def _location(self, content: str) -> str:
        return "Remote" if re.search(r"\bremote\b", content, re.I) else "Unknown Location"

    def _requirements(self, content: str) -> str:
        match = re.search(r"(?:need|requires?)\s+(.+?)(?:\.|$)", content, re.I)
        if not match:
            return ""
        value = match.group(1).strip()
        value = re.sub(r"\s*,?\s+and\s+", ", ", value)
        return value

    def _responsibilities(self, content: str) -> str:
        match = re.search(
            r"(?:role|you will)\s+(builds?|prototype|ship)\s+(.+?)(?:\.|Candidates|$)",
            content,
            re.I,
        )
        if not match:
            return ""
        verb = match.group(1).strip()
        rest = match.group(2).strip()
        return f"{verb} {rest}"
