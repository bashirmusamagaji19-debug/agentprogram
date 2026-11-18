"""Live web discovery invoked by SearchJobsTool, with bounded provider work."""

from __future__ import annotations

import asyncio
import re
from time import monotonic
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from web_task_agent.browser import BrowserUseClient, PageTimeoutError
from web_task_agent.deepseek_search import DeepSeekWebClient
from web_task_agent.job_sources import AggregatorPageLoader, DiscoveredJob
from web_task_agent.llm_extractor import build_llm_provider_config
from web_task_agent.models import BrowserPage
from web_task_agent.page_reliability import PageFailureCategory, classify_page_exception


def build_search_client() -> DeepSeekWebClient:
    config = build_llm_provider_config(provider="deepseek", model=None)
    return DeepSeekWebClient(api_key=config.api_key)


def _candidate_url(job: DiscoveredJob) -> str:
    parts = urlsplit(job.url)
    path = parts.path.casefold()
    if parts.fragment.startswith(("/", "!/")):
        path = urlsplit(parts.fragment.removeprefix("!")).path.casefold()
    # A search hit is only a hint. Omit obvious portals, articles and result lists;
    # unknown detail pages can still enter the normal extraction/link gates.
    if (parts.username or parts.password or path in {"", "/"}
            or any(marker in path for marker in ("/position/list", "/position/share", "/article/", "/news/"))
            or path.endswith(("opportunities.html", "top-talent.html"))):
        return ""
    params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
              if not k.casefold().startswith("utm_") and k.casefold() not in {"gclid", "fbclid"}]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))


class WebSearchBrowser:
    search_endpoint = "https://api.deepseek.com/v1/tools/search"
    def __init__(self, client: DeepSeekWebClient, *, official_api=None, http_loader=None,
                 repository=None, browser_loader=None, deadline=None, candidate_limit=10, min_batch_size=1):
        self.client = client
        self.official_api, self.http_loader, self.repository = official_api, http_loader, repository
        self.browser_loader = browser_loader or BrowserUseClient(deadline=deadline, render_wait_seconds=20)
        self.deadline = deadline
        self.candidate_limit = min(10, candidate_limit)
        self.min_batch_size = min(self.candidate_limit, min_batch_size)
        self.discovered_count = 0
        self.selected_count = 0
        self.candidate_summaries = {}
        self.search_diagnostics = {}
        self.source_diagnostics = []
        self.resolution_log = []
        self._jobs: dict[str, DiscoveredJob] = {}
        self._buffer: list[str] = []
        self._queries: list[str] = []
        self._search_calls = 0

    def _remaining(self):
        remaining = 30 if self.deadline is None else min(30, self.deadline - monotonic())
        if remaining <= 0:
            raise PageTimeoutError("web search deadline exceeded")
        return remaining

    def _search(self, query, count):
        # asyncio.run waits for executor threads on exit. Bound the synchronous
        # HTTP call too, including time already spent waiting for a worker.
        self.client.timeout = min(self.client.timeout, self._remaining())
        return self.client.search(query, count=count)

    async def search(self, query: str, target_count: int) -> list[BrowserPage]:
        self._remaining()
        if target_count <= 0:
            return []
        if not self._queries:
            base = re.sub(r"\s+(?:全国|不限)$", "", query.strip())
            self._queries = [f"{base} 招聘"[:128], f'{base} "职位详情"'[:128],
                             f"{base} site:jobs.feishu.cn OR site:mokahr.com"[:128]]
        while not self._buffer and self._search_calls < len(self._queries):
            actual_query = self._queries[self._search_calls]
            self._search_calls += 1
            try:
                jobs = await asyncio.wait_for(asyncio.to_thread(
                    self._search, actual_query, count=min(30, max(10, target_count * 3))),
                    timeout=self._remaining())
            except Exception as exc:
                status = getattr(exc, "api_status", None)
                self.source_diagnostics = [{
                    "source": "deepseek-search", "status": "failed", "attempts": self._search_calls,
                    "count": self.discovered_count, "candidate_count": self.discovered_count,
                    "error_type": type(exc).__name__,
                    "error_detail": f"DeepSeek Search HTTP {status}" if status else type(exc).__name__,
                }]
                raise
            for job in jobs:
                url = _candidate_url(job)
                if not url or url in self._jobs:
                    continue
                self._jobs[url] = DiscoveredJob(url=url, title=job.title, source="deepseek-search")
                self._buffer.append(url)
                self.candidate_summaries[url] = {"title": job.title, "source_spec": "deepseek-search"}
            self.discovered_count = len(self._jobs)
        count = max(0, min(max(target_count, self.min_batch_size), self.candidate_limit - self.selected_count))
        urls, self._buffer = self._buffer[:count], self._buffer[count:]
        self.selected_count += len(urls)
        self.search_diagnostics = {
            "query": query, "queries": self._queries[:self._search_calls], "provider": "deepseek-search",
            "search_calls": self._search_calls, "candidate_count": len(urls),
            "discovered_count": self.discovered_count, "selected_count": self.selected_count,
            "has_more": self.selected_count < self.candidate_limit and (
                bool(self._buffer) or self._search_calls < len(self._queries)),
            "stop_reason": "target_reached" if urls else "no_more_results",
        }
        self.source_diagnostics = [{
            "source": "deepseek-search", "status": "healthy" if self._jobs else "empty",
            "attempts": self._search_calls, "count": self.discovered_count,
            "candidate_count": self.discovered_count,
        }]
        return [BrowserPage(url="https://api.deepseek.com/v1/tools/search", title="Web search results",
                            content="Search result titles are unverified discovery hints.",
                            source="deepseek-search", metadata={"candidate_urls": urls,
                                                               "search_diagnostics": self.search_diagnostics})]

    async def open_url(self, url: str) -> BrowserPage:
        self._remaining()
        loader = AggregatorPageLoader(
            [self._jobs.get(url, DiscoveredJob(url=url, source="deepseek-search"))],
            official_api=self.official_api, http_loader=self.http_loader, repository=self.repository)
        try:
            return await asyncio.wait_for(loader(url), timeout=self._remaining())
        except Exception as exc:
            if classify_page_exception(exc).category is PageFailureCategory.REMOVED:
                raise
            # Render a JS-only detail page; this is still followed by a fresh,
            # independent JobLinkVerifier receipt before a result can be saved.
            page = await asyncio.wait_for(self.browser_loader.open_url(url), timeout=self._remaining())
            return page
        finally:
            self.resolution_log.extend(loader.resolution_log)
