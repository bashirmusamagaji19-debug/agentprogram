"""Live web discovery invoked by SearchJobsTool, with bounded provider work."""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from copy import copy
from dataclasses import replace
from time import monotonic
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from web_task_agent.browser import BrowserUseClient, HttpPageLoader, PageEmptyError, PageHttpError, PageTimeoutError
from web_task_agent.deepseek_search import DeepSeekWebClient
from web_task_agent.job_sources import AggregatorPageLoader, DiscoveredJob
from web_task_agent.llm_extractor import build_llm_provider_config
from web_task_agent.models import BrowserPage
from web_task_agent.official_api import OfficialApiContentFetcher
from web_task_agent.page_reliability import PageFailureCategory, classify_page_content, classify_page_exception
from web_task_agent.search_conditions import _internship_requested
from web_task_agent.source_catalog import VERIFIED_SOURCES, WEB_SEARCH_HOSTS
from web_task_agent.source_provenance import validate_public_url
from web_task_agent.verifier import is_non_development_role


def build_search_client() -> DeepSeekWebClient:
    config = build_llm_provider_config(provider="deepseek", model=None)
    return DeepSeekWebClient(api_key=config.api_key)


_COLLECTION_TITLE = re.compile(r"招聘(?:公告|简章|合集|专区|启动)|职位列表|岗位汇总|招聘官网|校园招聘$|"
                               r"careers home|job listings", re.I)
_DETAIL_PATH = re.compile(r"(?:^|/)(?:position|post|jobs?)/[^/]+/(?:detail|view)(?:/|$)|"
                          r"/jobs/detail/(?:intern|graduate|campus|social)/[0-9a-f-]{12,}(?:/|$)|"
                          r"/(?:jobs?|position|post)/[0-9a-z_-]*\d[0-9a-z_-]*(?:\.html)?(?:/|$)|"
                          r"/(?:job-?details?|position-?details?|details?|jobdesc|postdetail)/"
                          r"[^/]*\d[^/]*(?:/|$)", re.I)
_JOB_IDS = {"id", "jobid", "jobunionid", "positionid", "postid", "advertisementid", "positionintentionid", "zpxxid"}
_NON_DETAIL_SEGMENT = {"list", "search", "recruitment", "index", "home", "news", "announcement"}

_JOB_SECTION = re.compile(
    r"(?:^|\n)\s*(?:岗位职责|工作职责|主要职责|工作内容|职责描述|岗位描述|职位描述|任职要求|岗位要求|任职资格|"
    r"job description|what you.ll do|responsibilities|requirements|qualifications|about the role)"
    r"(?:[：:]|\s*(?:\n|$))", re.I)


def _has_detail_content(body: str, _title: str) -> bool:
    return bool(_JOB_SECTION.search(body))


def normalize_employer_host(value: str) -> str:
    """Accept the exact public recruitment hostname the user intends to add."""
    host = value.strip().casefold()
    if not re.fullmatch(r"(?=.{1,100}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                        r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", host):
        raise ValueError("请输入招聘网站完整域名，例如 careers.example.com，不能包含协议、路径或端口。")
    if host.rsplit(".", 1)[-1] in {"local", "localhost", "internal", "test", "invalid"}:
        raise ValueError("请填写公开招聘网站的域名。")
    if host in {"feishu.cn", "jobs.feishu.cn", "mokahr.com", "app.mokahr.com",
                "mioffice.cn", "jobs.f.mioffice.cn"}:
        raise ValueError("请选择对应公司的招聘子域名，不能直接加入整个平台。")
    return host


def _candidate_url(job: DiscoveredJob) -> str:
    parts = urlsplit(job.url)
    fragment = parts.fragment if parts.fragment.startswith(("/", "!/")) else ""
    route = urlsplit(fragment.removeprefix("!")) if fragment else parts
    path = route.path.casefold()
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.port is not None
            or parts.username or parts.password or path in {"", "/"}
            or any(marker in path for marker in ("/position/list", "/position/share", "/article/", "/news/"))
            or path.endswith(("opportunities.html", "top-talent.html"))):
        return ""
    identity_keys = {key.casefold() for key, val in parse_qsl(route.query) if val} & _JOB_IDS
    terminal = path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".html")
    if terminal in _NON_DETAIL_SEGMENT or not (_DETAIL_PATH.search(path) or identity_keys):
        return ""
    params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
              if not k.casefold().startswith("utm_") and k.casefold() not in {"gclid", "fbclid", "from"}]
    return urlunsplit((parts.scheme, parts.hostname, parts.path, urlencode(sorted(params)), fragment))


def _job_key(url: str) -> tuple:
    parts = urlsplit(url)
    route = urlsplit(parts.fragment.removeprefix("!")) if parts.fragment else parts
    ids = tuple(sorted((key.casefold(), value) for key, value in parse_qsl(route.query)
                       if key.casefold() in _JOB_IDS))
    path = route.path.rstrip("/")
    if parts.hostname == "zhaopin.meituan.com" and any(key == "jobunionid" for key, _ in ids):
        path = "/position/detail"
    return parts.hostname, path, ids


def _scoped_queries(query: str, hosts: tuple[str, ...]) -> list[str]:
    base = re.sub(r"\s+(?:全国|不限)$", "", query.strip())
    base = base[:min(70, 120 - max(map(len, hosts)))].rstrip()
    groups: list[str] = []
    batch = ""
    for host in hosts:
        scope = f"site:{host}"
        proposed = f"{batch} OR {scope}" if batch else scope
        if batch and len(f"{base} ({proposed})") > 128:
            groups.append(f"{base} ({batch})")
            batch = scope
        else:
            batch = proposed
    if batch:
        groups.append(f"{base} ({batch})")
    return groups


class WebSearchBrowser:
    search_endpoint = "https://api.deepseek.com/v1/tools/search"
    def __init__(self, client: DeepSeekWebClient, *, official_api=None, http_loader=None,
                 repository=None, browser_loader=None, deadline=None, candidate_limit=10, min_batch_size=1,
                 extra_hosts=(), official_discovery=None):
        self.client = client
        self.official_api, self.http_loader, self.repository = official_api, http_loader, repository
        self.browser_loader = browser_loader or BrowserUseClient(
            deadline=deadline, render_wait_seconds=8, content_ready=_has_detail_content)
        self.deadline = deadline
        self.candidate_limit = min(10, candidate_limit)
        self.min_batch_size = min(self.candidate_limit, min_batch_size)
        self.discovered_count = 0
        self.selected_count = 0
        self.candidate_summaries = {}
        self.search_diagnostics = {}
        self.source_diagnostics = []
        self.resolution_log = []
        self.hosts = tuple(dict.fromkeys((
            *(normalize_employer_host(host) for host in extra_hosts),
            *(host for source in VERIFIED_SOURCES for host in WEB_SEARCH_HOSTS.get(source, ())),
        )))
        self._keys = set()
        self._hit_counts = Counter()
        self._selected_employers = Counter()
        self._employer_by_host = {host: spec for spec, hosts in WEB_SEARCH_HOSTS.items() for host in hosts}
        self._jobs: dict[str, DiscoveredJob] = {}
        self._buffer: list[str] = []
        self._queries: list[str] = []
        self._search_calls = 0
        self._search_errors: list[str] = []
        self._official_discovery = official_discovery
        self._supplement_attempted = False
        self._want_internship = False
        self._official_diagnostics = []

    def discovered_job(self, url: str) -> DiscoveredJob | None:
        job = self._jobs.get(url)
        return job if job is not None and job.source != "deepseek-search" else None

    def _queue_job(self, job: DiscoveredJob, *, official: bool = False) -> None:
        try:
            if urlsplit(job.url).hostname not in self.hosts:
                self._hit_counts["filtered_outside_employers"] += 1
                return
            validate_public_url(job.url)
            url = _candidate_url(job)
            if url:
                validate_public_url(url)
        except ValueError:
            self._hit_counts["filtered_invalid_url"] += 1
            return
        if not url or _COLLECTION_TITLE.search(job.title):
            self._hit_counts["filtered_not_detail"] += 1
            return
        parts = urlsplit(url)
        if (self._want_internship and parts.hostname == "talent.baidu.com"
                and not parts.path.casefold().startswith("/jobs/detail/intern/")):
            self._hit_counts["filtered_wrong_recruitment_type"] += 1
            return
        if is_non_development_role(job.title):
            self._hit_counts["filtered_non_development_title"] += 1
            return
        key = _job_key(url)
        if key in self._keys:
            self._hit_counts["filtered_duplicate"] += 1
            return
        self._keys.add(key)
        title = re.sub(r"[\x00-\x1f\x7f]", " ", job.title).strip()[:500]
        company = VERIFIED_SOURCES.get(self._employer_by_host.get(parts.hostname), {}).get("name", "")
        self._jobs[url] = (replace(job, url=url, title=title) if official else DiscoveredJob(
            url=url, title=title, company=company, source="deepseek-search"))
        self._buffer.append(url)
        self.candidate_summaries[url] = {"title": title, "source_spec": self._jobs[url].source}
        self.discovered_count = len(self._jobs)
        if official:
            self._hit_counts["official_supplement_candidates"] += 1

    async def _supplement(self, count: int) -> None:
        self._supplement_attempted = True
        try:
            jobs, self._official_diagnostics = await asyncio.wait_for(
                self._official_discovery(count), timeout=min(15, self._remaining()))
        except TimeoutError:
            self._official_diagnostics = [{"source": "official-supplement", "status": "failed",
                                           "error_category": "timeout", "count": 0}]
            return
        for job in jobs:
            self._queue_job(job, official=True)

    def _remaining(self):
        remaining = 30 if self.deadline is None else min(30, self.deadline - monotonic())
        if remaining <= 0:
            raise PageTimeoutError("web search deadline exceeded")
        return remaining

    def _search(self, query, count):
        # asyncio.run waits for executor threads on exit. Bound the synchronous
        # HTTP call too, including time already spent waiting for a worker.
        original = self.client.timeout
        try:
            self.client.timeout = min(original, 8, self._remaining())
            return self.client.search(query, count=count)
        finally:
            self.client.timeout = original

    async def search(self, query: str, target_count: int) -> list[BrowserPage]:
        self._remaining()
        if target_count <= 0:
            return []
        if not self._queries:
            self._queries = _scoped_queries(query, self.hosts)
            self._want_internship = _internship_requested(query)
        count = max(0, min(5, max(target_count, self.min_batch_size), self.candidate_limit - self.selected_count))
        if count and self._search_calls and self._official_discovery and not self._supplement_attempted:
            await self._supplement(count)
        while count and len(self._buffer) < count and self._search_calls < len(self._queries):
            actual_query = self._queries[self._search_calls]
            self._search_calls += 1
            try:
                jobs = await asyncio.wait_for(asyncio.to_thread(
                    self._search, actual_query, count=min(30, max(10, target_count * 3))),
                    timeout=self._remaining())
            except Exception as exc:
                status = getattr(exc, "api_status", None)
                detail = f"DeepSeek Search HTTP {status}" if status else type(exc).__name__
                self._search_errors.append(detail)
                # A failed supplemental query cannot erase a usable earlier batch.
                if self._buffer:
                    break
                self.source_diagnostics = [{
                    "source": "deepseek-search", "status": "failed", "attempts": self._search_calls,
                    "count": self.discovered_count, "candidate_count": self.discovered_count,
                    "error_type": type(exc).__name__,
                    "error_detail": detail,
                }]
                raise
            for job in jobs:
                self._hit_counts["returned_hits"] += 1
                self._queue_job(job)
        if count and not self._buffer and self._official_discovery and not self._supplement_attempted:
            await self._supplement(count)
        # Respect result rank within each employer, but give another employer a
        # turn before processing repeated hits from the first company.
        urls = []

        def employer(url):
            host = urlsplit(url).hostname
            return self._employer_by_host.get(host, host)

        while self._buffer and len(urls) < count:
            index = min(range(len(self._buffer)), key=lambda i: (
                self._jobs[self._buffer[i]].source == "deepseek-search",
                self._selected_employers[employer(self._buffer[i])],
            ))
            url = self._buffer.pop(index)
            urls.append(url)
            self._selected_employers[employer(url)] += 1
        self.selected_count += len(urls)
        self.search_diagnostics = {
            "query": query, "queries": self._queries[:self._search_calls], "provider": "deepseek-search",
            "search_calls": self._search_calls, "candidate_count": len(urls),
            "discovered_count": self.discovered_count, "selected_count": self.selected_count,
            "has_more": self.selected_count < self.candidate_limit and (
                bool(self._buffer) or self._search_calls < len(self._queries)
                or (self._official_discovery is not None and not self._supplement_attempted)),
            "stop_reason": "batch_selected" if urls else "no_more_results",
            "employer_hosts": list(self.hosts), "query_errors": list(self._search_errors), **self._hit_counts,
        }
        self.source_diagnostics = [{
            "source": "deepseek-search", "status": "healthy" if self._hit_counts["returned_hits"] else "empty",
            "attempts": self._search_calls, "count": self._hit_counts["returned_hits"],
            "candidate_count": self.discovered_count - self._hit_counts["official_supplement_candidates"],
        }, *self._official_diagnostics]
        return [BrowserPage(url="https://api.deepseek.com/v1/tools/search", title="Web search results",
                            content="Discovery hints. Verify each job detail independently.",
                            source="deepseek-search", metadata={"candidate_urls": urls,
                                                               "search_diagnostics": self.search_diagnostics})]

    async def open_url(self, url: str) -> BrowserPage:
        if url not in self._jobs:
            raise PageHttpError("candidate outside the employer search results", status_code=403)
        detail_timeout = min(8, self._remaining())
        detail_deadline = monotonic() + detail_timeout
        api, http = self.official_api, self.http_loader
        if isinstance(api, OfficialApiContentFetcher):
            api = copy(api)
            api.deadline = detail_deadline
        if isinstance(http, HttpPageLoader):
            http = copy(http)
            http.deadline = detail_deadline
        loader = AggregatorPageLoader([self._jobs[url]], official_api=api, http_loader=http,
                                      repository=self.repository)
        try:
            page = await asyncio.wait_for(loader(url), timeout=detail_timeout)
            if (isinstance(http, HttpPageLoader) and page.metadata.get("content_origin") == "http"
                    and not _has_detail_content(page.content, page.title)):
                raise PageEmptyError("HTTP returned no identifiable job description")
        except Exception as exc:
            if classify_page_exception(exc).category is PageFailureCategory.REMOVED:
                raise
            # Render a JS-only detail page; this is still followed by a fresh,
            # independent JobLinkVerifier receipt before a result can be saved.
            try:
                page = await asyncio.wait_for(self.browser_loader.open_url(url), timeout=min(12, self._remaining()))
            except TimeoutError as timeout:
                raise PageTimeoutError("candidate detail timed out") from timeout
            if not _has_detail_content(page.content, page.title):
                failure = classify_page_content(page)
                if failure and failure.category is PageFailureCategory.REMOVED:
                    raise PageHttpError("candidate is no longer available", status_code=404) from None
                raise PageEmptyError("browser returned no identifiable job description") from None
        finally:
            self.resolution_log.extend(loader.resolution_log)
        actual = page.metadata.get("canonical_url") or page.url
        if urlsplit(actual).hostname not in self.hosts or urlsplit(page.url).hostname not in self.hosts:
            raise PageHttpError("candidate redirected outside the selected employers", status_code=403)
        return page
