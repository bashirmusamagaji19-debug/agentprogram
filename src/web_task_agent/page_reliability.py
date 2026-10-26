"""Conservative page-failure classification without exposing request contents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.error import URLError

from web_task_agent.browser import (
    MIN_USEFUL_CONTENT_CHARS,
    BrowserConfigurationError,
    PageEmptyError,
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
    http_status: int | None = None


class PageRemovedError(BrowserConfigurationError):
    """A strong detail-page removal signal; list content cannot override it."""


def classify_page_exception(error: Exception) -> PageFailure:
    # Never inspect arbitrary exception text, URLs or chained business errors.
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    if status in (404, 410) or isinstance(error, PageRemovedError):
        category = PageFailureCategory.REMOVED
    elif status in (401, 403, 429):
        category = PageFailureCategory.BLOCKED
    elif (isinstance(status, int) and 500 <= status <= 599) or isinstance(
        error, (PageTimeoutError, TimeoutError, ConnectionError, URLError)
    ):
        category = PageFailureCategory.TRANSIENT
    elif isinstance(error, PageEmptyError):
        category = PageFailureCategory.EMPTY_SHELL
    else:
        category = PageFailureCategory.UNKNOWN
    detail = f"HTTP {status}" if isinstance(status, int) else type(error).__name__
    return PageFailure(category, detail, status if isinstance(status, int) and not isinstance(status, bool) else None)


_TOMBSTONE = re.compile(
    r"^(?:该|此|本|您访问的|你访问的)?(?:职位|岗位|页面|链接)"
    r"(?:已经|已|已经被|已被)?(?:下线|关闭|删除|失效|不存在|不再开放)(?=$|[\s，。！!,.：:;；-])"
    r"|^(?:this\s+)?(?:job|position)(?:\s+posting)?\s+(?:is\s+)?"
    r"(?:no longer available|closed|not found|has been removed)(?=$|[\s!,.:-])"
    r"|^(?:404\s*[-:：]?\s*)?(?:page not found|not found)$",
    re.IGNORECASE,
)


def classify_page_content(page: BrowserPage) -> PageFailure | None:
    content = re.sub(r"\s+", " ", page.content).strip()
    title = re.sub(r"\s+", " ", page.title).strip()
    # A long JD may discuss error handling; do not mistake a mention for a tombstone.
    if len(content) <= 240 and (_TOMBSTONE.match(content) or _TOMBSTONE.match(title)):
        return PageFailure(PageFailureCategory.REMOVED, "explicit job/page removal notice")
    if len(content) < MIN_USEFUL_CONTENT_CHARS:
        return PageFailure(PageFailureCategory.EMPTY_SHELL, "page has insufficient content")
    return None
