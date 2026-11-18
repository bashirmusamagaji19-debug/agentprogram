"""Bounded DeepSeek Search/Fetch adapters; no changes to production defaults."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from web_task_agent.browser import MIN_USEFUL_CONTENT_CHARS, BrowserConfigurationError, PageEmptyError
from web_task_agent.job_sources import DiscoveredJob
from web_task_agent.models import BrowserPage
from web_task_agent.page_reliability import (
    PageFailureCategory,
    PageRemovedError,
    classify_page_content,
    classify_page_exception,
)

Transport = Callable[[str, str, dict[str, Any], int], dict[str, Any]]


class DeepSeekApiError(BrowserConfigurationError):
    """Provider failure, deliberately not a target-site HTTP removal signal."""

    def __init__(self, api_status: int | None = None):
        self.api_status = api_status
        super().__init__(f"DeepSeek API error ({api_status or 'transport'})")


def _transport(endpoint: str, key: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    req = Request(
        f"https://api.deepseek.com/v1/tools/{endpoint}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.loads(response.read())
    except HTTPError as error:
        # Do not read/log the response body or expose credentials in exception chains.
        raise DeepSeekApiError(error.code) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise DeepSeekApiError() from None


def _web_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username
    except ValueError:
        return False


class DeepSeekWebClient:
    def __init__(self, *, api_key: str, timeout: int = 20, transport: Transport | None = None):
        if not api_key.strip():
            raise ValueError("DEEPSEEK_API_KEY is required")
        self._api_key = api_key
        self.timeout = timeout
        self.transport = transport or _transport
        self.usage = {endpoint: {"attempts": 0, "billable": 0} for endpoint in ("search", "fetch")}
        self._last_call = 0.0

    def _call(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        # Sequential calls stay below the documented account-level 5 QPS limit.
        if self.transport is _transport:
            time.sleep(max(0.0, 0.22 - (time.monotonic() - self._last_call)))
        self.usage[endpoint]["attempts"] += 1
        self._last_call = time.monotonic()
        return self.transport(endpoint, self._api_key, payload, self.timeout)

    def search(self, query: str, *, count: int = 10) -> list[DiscoveredJob]:
        if not 1 <= len(query.strip()) <= 128 or not 1 <= count <= 50:
            raise ValueError("query must be 1..128 characters; count must be 1..50")
        payload = self._call("search", {"query": query, "count": count, "need_content": False})
        results = payload.get("results", [])
        if not isinstance(results, list):
            raise DeepSeekApiError()
        if results:
            self.usage["search"]["billable"] += 1
        jobs = []
        seen = set()
        for item in results[:count]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not _web_url(url) or url in seen:
                continue
            seen.add(url)
            # Search summaries and publication dates are not verified job facts.
            jobs.append(DiscoveredJob(
                url=url, title=str(item.get("title") or ""), source="deepseek-search",
            ))
        return jobs

    def fetch(self, url: str) -> BrowserPage:
        if not _web_url(url) or len(url) > 8192:
            raise ValueError("Fetch requires an HTTP(S) URL without credentials")
        payload = self._call("fetch", {"url": url})
        content = payload.get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise PageEmptyError("DeepSeek Fetch returned no body")
        self.usage["fetch"]["billable"] += 1
        return BrowserPage(
            url=url, title=str(payload.get("title") or ""), content=content, source="deepseek-fetch",
            metadata={"content_origin": "deepseek-fetch"},
        )


class FetchFallbackLoader:
    """Try Fetch only after the existing content chain fails or returns a shell."""

    def __init__(
        self, primary: Callable[[str], Awaitable[BrowserPage]], client: DeepSeekWebClient, *, max_calls: int = 10,
    ):
        self.primary = primary
        self.client = client
        self.max_calls = max_calls
        self.calls = 0
        self.diagnostics: list[dict[str, Any]] = []

    async def __call__(self, url: str) -> BrowserPage:
        try:
            page = await self.primary(url)
            failure = classify_page_content(page)
            if failure and failure.category == PageFailureCategory.REMOVED:
                raise PageRemovedError("explicit removal notice")
            if len(page.content.strip()) >= MIN_USEFUL_CONTENT_CHARS:
                return page
            raise PageEmptyError("primary page has insufficient content")
        except BrowserConfigurationError as error:
            if classify_page_exception(error).category == PageFailureCategory.REMOVED:
                raise
            if self.calls >= self.max_calls:
                raise PageEmptyError("Fetch budget exhausted") from None
        self.calls += 1
        try:
            page = self.client.fetch(url)
            failure = classify_page_content(page)
            if failure and failure.category == PageFailureCategory.REMOVED:
                raise PageRemovedError("Fetch returned an explicit removal notice")
            if len(page.content.strip()) < MIN_USEFUL_CONTENT_CHARS:
                raise PageEmptyError("Fetch returned insufficient content")
        except BrowserConfigurationError as error:
            self.diagnostics.append({"url": url, "stage": "fetch", "error_type": type(error).__name__})
            raise
        self.diagnostics.append({"url": url, "stage": "fetch", "status": "recovered"})
        return page
