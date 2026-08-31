"""岗位发现源（aggregator connectors）。

阶段 1 设计（见 docs/superpowers/plans/2026-08-31-chinese-job-pipeline.md）：
- `JobSource` Protocol：统一的发现接口，`discover(limit)` 返回 `DiscoveredJob` 列表
- `AggregatorRepoSource`：解析 GitHub 聚合仓库发布的 JSON 岗位库
  （当前适配 Jasmine-Liu-min/job-radar 的 data/jobs.json 格式：
  `{"schema_version": int, "updated_at": str, "jobs": [...]}`，
  字段含 official_url / jd_text / company_name / title / location / publish_time）

过滤口径：标题含"实习"且命中 AI 方向关键词。aggregator 自带的 jd_text
作为可选兜底内容——官方详情页多为 JS 渲染（阶段 0 实测 5/5 empty_page），
HttpPageLoader 失败时用 jd_text 构造 BrowserPage，保证抽取链路有输入。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib import request as url_request
from urllib.error import URLError

from web_task_agent.keywords import AI_JOB_KEYWORDS, INTERN_TITLE_KEYWORDS

# 发现侧标题过滤与 verifier 共用同一口径（keywords.py）
AI_TITLE_KEYWORDS = AI_JOB_KEYWORDS


@dataclass(frozen=True)
class DiscoveredJob:
    url: str
    title: str = ""
    company: str = ""
    location: str = ""
    posted_at: str = ""
    jd_text: str = ""  # aggregator 提供的 JD 兜底内容，可为空
    source: str = "aggregator"
    tags: list[str] = field(default_factory=list)


class JobSource(Protocol):
    async def discover(self, limit: int) -> list[DiscoveredJob]:
        """发现岗位，返回至多 limit 条。"""
        ...


def is_intern_ai_job(title: str) -> bool:
    """标题过滤口径：实习 + AI 方向。"""
    if not any(keyword in title for keyword in INTERN_TITLE_KEYWORDS):
        return False
    return any(keyword in title for keyword in AI_TITLE_KEYWORDS)


class AggregatorRepoSource:
    """读取聚合仓库的 jobs.json（本地路径或 raw URL）。

    Args:
        source_location: 本地文件路径或 HTTP(S) URL，指向聚合仓库发布的 JSON。
        source_name: 记录用来源标识。
    """

    def __init__(self, source_location: str, *, source_name: str = "job-radar") -> None:
        self.source_location = source_location
        self.source_name = source_name

    async def discover(self, limit: int) -> list[DiscoveredJob]:
        if limit <= 0:
            return []
        payload = self._load_payload()
        return self._parse_jobs(payload)[:limit]

    def _load_payload(self) -> dict:
        location = self.source_location.strip()
        if location.startswith("http://") or location.startswith("https://"):
            with url_request.urlopen(location, timeout=30) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        return json.loads(Path(location).read_text(encoding="utf-8"))

    def _parse_jobs(self, payload: dict | list) -> list[DiscoveredJob]:
        # 兼容两种发布格式：{"schema_version":…, "jobs":[…]} 或裸数组 […]
        raw_jobs = payload.get("jobs") if isinstance(payload, dict) else payload
        if not isinstance(raw_jobs, list):
            return []
        discovered: list[DiscoveredJob] = []
        seen_urls: set[str] = set()
        for raw in raw_jobs:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            url = str(raw.get("official_url") or "").strip()
            if not url or not title or not is_intern_ai_job(title):
                continue
            url_key = url.rstrip("/").lower()
            if url_key in seen_urls:
                continue
            seen_urls.add(url_key)
            discovered.append(
                DiscoveredJob(
                    url=url,
                    title=title,
                    company=str(raw.get("company_name") or "").strip(),
                    location=str(raw.get("location") or "").strip(),
                    posted_at=str(raw.get("publish_time") or "").strip(),
                    jd_text=str(raw.get("jd_text") or "").strip(),
                    source=self.source_name,
                    tags=[str(tag) for tag in raw.get("tags", []) if tag],
                )
            )
        return discovered


def build_discovered_page(job: DiscoveredJob) -> "object":
    """用 DiscoveredJob 的兜底内容构造 BrowserPage（延迟导入避免循环依赖）。

    用于 HttpPageLoader 拿不到正文（JS 渲染页）时的 fallback：
    aggregator 的 jd_text 直接进抽取链路，URL 保持官方详情页。
    """
    from web_task_agent.models import BrowserPage

    return BrowserPage(
        url=job.url,
        title=job.title,
        content=job.jd_text,
        source=f"aggregator:{job.source}",
        metadata={"discovered_title": job.title, "discovered_company": job.company},
    )


# 低于该长度的正文视为"无有效内容"（美团/腾讯 SPA 返回 4~11 字符的壳；
# 实测 job-radar 的 jd_text 中位数仅 46 字符——基本全是"标题+部门+城市"，
# 50 门槛会把这类无信息内容和第三方站的浏览器兼容提示（98 字符）放进管线，
# 诱发 LLM 幻觉 JD，复现实录 #21）
MIN_USEFUL_CONTENT_CHARS = 120


class AggregatorPageLoader:
    """聚合源页面的内容解析链：缓存 → 官方 API → HttpPageLoader → jd_text 兜底。

    按 URL 逐级尝试，每个 URL 记录最终使用的策略（resolution_log），
    全部失败抛 PageEmptyError（进入既有失败分类体系）。
    """

    def __init__(
        self,
        jobs: list[DiscoveredJob],
        *,
        official_api: object | None = None,
        http_loader: object | None = None,
        repository: object | None = None,
        max_age_hours: float = 24.0,
    ) -> None:
        self._jobs_by_url = {job.url: job for job in jobs}
        self._official_api = official_api
        self._http_loader = http_loader
        self._repository = repository
        self._max_age_hours = max_age_hours
        self.resolution_log: list[dict[str, str]] = []

    async def __call__(self, url: str) -> "object":
        from web_task_agent.browser import PageEmptyError

        job = self._jobs_by_url.get(url)
        if job is None:
            # 非 aggregator 发现的 URL 直接走普通 loader（与旧行为一致）
            if self._http_loader is None:
                raise PageEmptyError(f"no loader for non-aggregator URL: {url}")
            return await self._http_loader(url)

        # 1. 缓存
        if self._repository is not None:
            cached = self._repository.get_cached_page(url, max_age_hours=self._max_age_hours)
            if cached is not None:
                self._record(url, "cache")
                return cached

        # 2. 官方 API（支持域名的 SPA 页优先走结构化接口）
        if self._official_api is not None:
            try:
                content = await self._official_api.fetch(url)
            except Exception as exc:  # noqa: BLE001
                if type(exc).__name__ != "UnsupportedOfficialApiError":
                    self._record(url, f"official-api-failed:{type(exc).__name__}")
                else:
                    self._record(url, "official-api:unsupported")
            else:
                page = self._to_page(url, content.content, content.title, "official-api")
                self._cache(url, page)
                self._record(url, "official-api")
                return page

        # 3. HttpPageLoader（服务端渲染站点可直达）
        if self._http_loader is not None:
            try:
                page = await self._http_loader(url)
            except Exception as exc:  # noqa: BLE001
                self._record(url, f"http-failed:{type(exc).__name__}")
            else:
                if len(page.content.strip()) >= MIN_USEFUL_CONTENT_CHARS:
                    self._cache(url, page)
                    self._record(url, "http")
                    return page
                self._record(url, "http:empty-page")

        # 4. aggregator jd_text 兜底（同样要求达到有效内容门槛——
        #    job-radar 的 jd_text 多为"标题+部门+城市"标题行，无 JD 信息，
        #    放进管线只会诱发 LLM 幻觉）
        if len(job.jd_text.strip()) >= MIN_USEFUL_CONTENT_CHARS:
            page = build_discovered_page(job)
            self._cache(url, page)
            self._record(url, "jd_text-fallback")
            return page
        if job.jd_text.strip():
            self._record(url, "jd_text:too-short")

        raise PageEmptyError(
            f"aggregator page has no usable content "
            f"(official API/http/jd_text all failed or below {MIN_USEFUL_CONTENT_CHARS} chars): {url}"
        )

    def _to_page(self, url: str, content: str, title: str, source: str) -> "object":
        from web_task_agent.models import BrowserPage

        job = self._jobs_by_url.get(url)
        return BrowserPage(
            url=url,
            title=title or (job.title if job else ""),
            content=content,
            source=source,
        )

    def _cache(self, url: str, page: "object") -> None:
        if self._repository is not None:
            self._repository.cache_page(page)  # type: ignore[arg-type]

    def _record(self, url: str, strategy: str) -> None:
        self.resolution_log.append({"url": url, "strategy": strategy})
