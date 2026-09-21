from __future__ import annotations

import re
import socket
from dataclasses import dataclass
from enum import StrEnum
from urllib.error import HTTPError, URLError

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
    r"(?i)(?P<quote>[\"']?)"
    r"\b(?P<key>"
    r"(?:id|auth|session|refresh|access)[-_ ]?token|"
    r"client[-_ ]?secret|secret|"
    r"x[-_]amz[-_]signature|signature|"
    r"cookie|set[-_]cookie|"
    r"token|authorization|api[-_ ]?key|password"
    r")\b"
    r"(?P=quote)(?P<separator>\s*[:=]\s*)"
    r"(?:(?:bearer|basic)\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;}&#]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_BASIC_PATTERN = re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=._~-]+")
_URL_USERINFO_PATTERN = re.compile(
    r"(?i)(?P<prefix>[a-z][a-z0-9+.-]*://[^/\s:@]+:)"
    r"(?P<password>[^@\s/]+)"
    r"(?P<suffix>@[^/\s]+)"
)
_COOKIE_PATTERN = re.compile(
    r"(?i)(?P<header>\b(?:set-cookie|cookie)\s*:\s*)[^,\r\n]+"
)
_WINDOWS_LOCAL_PATH_PATTERN = re.compile(
    r"(?i)(?<!\w)"
    r"(?:"
    r"[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/]"
    r"|\\\\[^\\/\s]+\\[^\\/\s]+\\"
    r")"
    r"[^\r\n\"',;)}]*?\.[A-Za-z0-9]{1,8}"
    r"(?=$|[\s,;)\]}\"'])"
)
_POSIX_LOCAL_PATH_PATTERN = re.compile(
    r"(?<!\w)/(?:Users|home|var|tmp|root)/"
    r"(?:"
    r"[^\r\n\"',;)}]*?\.[A-Za-z0-9]{1,8}"
    r"|"
    r"(?:[^/\r\n\"',;)}]+/)*[^/\s\r\n\"',;)}]+"
    r")"
    r"(?=$|[\s,;)\]}\"'])"
)


def safe_diagnostic_detail(value: object, max_chars: int = 160) -> str:
    normalized = " ".join(str(value).split())
    redacted = _SECRET_PATTERN.sub(
        r"\g<quote>\g<key>\g<quote>\g<separator><redacted>",
        normalized,
    )
    redacted = _BEARER_PATTERN.sub("Bearer <redacted>", redacted)
    redacted = _BASIC_PATTERN.sub("Basic <redacted>", redacted)
    redacted = _URL_USERINFO_PATTERN.sub(
        r"\g<prefix><redacted>\g<suffix>",
        redacted,
    )
    redacted = _COOKIE_PATTERN.sub(r"\g<header><redacted>", redacted)
    redacted = _WINDOWS_LOCAL_PATH_PATTERN.sub("<local-path>", redacted)
    redacted = _POSIX_LOCAL_PATH_PATTERN.sub("<local-path>", redacted)
    return redacted[: max(0, max_chars)]


def _http_status_category(status_code: int | None) -> PageFailureCategory | None:
    if status_code in {404, 410}:
        return PageFailureCategory.REMOVED
    if status_code in {401, 403, 429}:
        return PageFailureCategory.BLOCKED
    if status_code is not None and status_code >= 500:
        return PageFailureCategory.TRANSIENT
    return None


def classify_page_exception(exc: Exception) -> PageFailure:
    detail = safe_diagnostic_detail(exc)
    if isinstance(exc, PageHttpError):
        category = _http_status_category(exc.status_code)
        if category is not None:
            return PageFailure(category, detail)
    if isinstance(exc, HTTPError):
        category = _http_status_category(exc.code)
        if category is not None:
            return PageFailure(category, detail)
    if isinstance(exc, PageTimeoutError):
        return PageFailure(PageFailureCategory.TRANSIENT, detail)
    if isinstance(exc, PageEmptyError):
        return PageFailure(PageFailureCategory.EMPTY_SHELL, detail)
    if "E1005" in str(exc) or "post unavailable" in str(exc).casefold():
        return PageFailure(PageFailureCategory.REMOVED, detail)
    if isinstance(exc, HTTPError):
        return PageFailure(PageFailureCategory.UNKNOWN, detail)
    if isinstance(
        exc,
        (TimeoutError, ConnectionError, socket.gaierror, URLError),
    ):
        return PageFailure(PageFailureCategory.TRANSIENT, detail)
    return PageFailure(PageFailureCategory.UNKNOWN, detail)


def classify_page_content(page: BrowserPage) -> PageFailure | None:
    content = page.content.strip()
    title_text = page.title.casefold()
    if any(marker in title_text for marker in _TOMBSTONE_MARKERS):
        return PageFailure(PageFailureCategory.REMOVED, "explicit removal page")

    if len(content) >= MIN_USEFUL_CONTENT_CHARS:
        return None

    if any(marker in content.casefold() for marker in _TOMBSTONE_MARKERS):
        return PageFailure(PageFailureCategory.REMOVED, "explicit removal page")
    return PageFailure(
        PageFailureCategory.EMPTY_SHELL,
        "page below useful-content threshold",
    )
