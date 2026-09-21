from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from web_task_agent.browser import (
    MIN_USEFUL_CONTENT_CHARS,
    PageEmptyError,
    PageHttpError,
    PageTimeoutError,
)
from web_task_agent.models import BrowserPage


class PageFailureCategory(StrEnum):
    REMOVED = "removed"
    BLOCKED = "blocked"
    TRANSIENT = "transient"
    EMPTY_SHELL = "empty_shell"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PageFailure:
    category: PageFailureCategory
    detail: str

    @property
    def is_permanent(self) -> bool:
        return self.category is PageFailureCategory.REMOVED


class PageRemovedError(RuntimeError):
    """The job detail URL has strong evidence of permanent removal."""


_TOMBSTONE_MARKERS = (
    "职位已下线",
    "岗位已下线",
    "职位不存在",
    "岗位不存在",
    "页面不存在",
    "job is no longer available",
    "position is no longer available",
)
_SECRET_PATTERN = re.compile(
    r"(?i)(\bauthorization\b|\bapi[-_ ]?key\b|\baccess[-_ ]?token\b|\btoken\b)"
    r"(\s*[:=]\s*)(?:(?:bearer|basic)\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)


def safe_diagnostic_detail(value: object, max_chars: int = 160) -> str:
    normalized = " ".join(str(value).split())
    redacted = _SECRET_PATTERN.sub(r"\1=<redacted>", normalized)
    return redacted[: max(0, max_chars)]


def classify_page_exception(exc: Exception) -> PageFailure:
    detail = safe_diagnostic_detail(exc)
    if isinstance(exc, PageHttpError):
        if exc.status_code in {404, 410}:
            return PageFailure(PageFailureCategory.REMOVED, detail)
        if exc.status_code in {401, 403, 429}:
            return PageFailure(PageFailureCategory.BLOCKED, detail)
        if exc.status_code is not None and exc.status_code >= 500:
            return PageFailure(PageFailureCategory.TRANSIENT, detail)
    if isinstance(exc, PageTimeoutError):
        return PageFailure(PageFailureCategory.TRANSIENT, detail)
    if isinstance(exc, PageEmptyError):
        return PageFailure(PageFailureCategory.EMPTY_SHELL, detail)
    if "E1005" in str(exc) or "post unavailable" in str(exc).casefold():
        return PageFailure(PageFailureCategory.REMOVED, detail)
    return PageFailure(PageFailureCategory.UNKNOWN, detail)


def classify_page_content(page: BrowserPage) -> PageFailure | None:
    content = page.content.strip()
    if len(content) >= MIN_USEFUL_CONTENT_CHARS:
        return None

    short_text = f"{page.title}\n{content}".casefold()
    if any(marker in short_text for marker in _TOMBSTONE_MARKERS):
        return PageFailure(PageFailureCategory.REMOVED, "explicit removal page")
    return PageFailure(
        PageFailureCategory.EMPTY_SHELL,
        "page below useful-content threshold",
    )
