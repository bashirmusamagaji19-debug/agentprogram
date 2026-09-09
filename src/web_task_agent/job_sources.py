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

from web_task_agent.browser import MIN_USEFUL_CONTENT_CHARS
from web_task_agent.keywords import (
    AI_JOB_KEYWORDS,
    INTERN_TITLE_KEYWORDS,
    classify_company_tier,
)

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
    tier: str = ""
    content_origin: str = ""  # 正文来源:loader 各分支/detail-probed(liser 内探测)  # 公司梯队:大厂/车企/具身智能/AI 中厂/初创/中小厂(长尾)


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
                    tier=classify_company_tier(str(raw.get("company_name") or "")),
                )
            )
        return discovered


def build_discovered_page(job: DiscoveredJob) -> object:
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
        metadata={
            "discovered_title": job.title,
            "discovered_company": job.company,
            "tier": job.tier,
            "content_origin": job.content_origin,
        },
    )


# MIN_USEFUL_CONTENT_CHARS 统一定义在 browser.py（抓取与缓存两条路径共用，
# #25：门槛收紧前写入的旧壳页缓存曾绕过门槛，空壳页互判重复）


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

    async def __call__(self, url: str) -> object:
        from web_task_agent.browser import PageEmptyError

        job = self._jobs_by_url.get(url)
        if job is None:
            # 非 aggregator 发现的 URL 直接走普通 loader（与旧行为一致）
            if self._http_loader is None:
                raise PageEmptyError(f"no loader for non-aggregator URL: {url}")
            return await self._http_loader(url)

        # 1. 缓存（命中也过内容门槛——旧壳页缓存不生效，#25）
        if self._repository is not None:
            cached = self._repository.get_cached_page(url, max_age_hours=self._max_age_hours)
            if (
                cached is not None
                and len(cached.content.strip()) >= MIN_USEFUL_CONTENT_CHARS
            ):
                self._record(url, "cache")
                return cached  # origin 已在缓存 metadata 中(storage 列透传)

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
                # 与 http/jd_text 路径同一门槛（#27）：短官方 API 正文既不能
                # 进管线（100~119 字符区间绕过 extractor 的 LLM 幻觉护栏），
                # 也不能写缓存——否则门槛检查会让该 URL 的缓存永远失效，
                # 每次运行都重调官方 API
                if len(content.content.strip()) >= MIN_USEFUL_CONTENT_CHARS:
                    # canonical_url：内容实际来源的可浏览详情页（如腾讯校招岗
                    # 内容来自 join.qq.com，careers 详情页对校招 postId 404）。
                    # BrowserPage.url 保持"请求 URL"语义（缓存键/诊断一致），
                    # canonical 经 metadata 透传，extractor 构造 JobPosting 时落位。
                    canonical = str(getattr(content, "canonical_url", "") or "").strip()
                    page = self._to_page(url, content.content, content.title, "official-api")
                    page = page.model_copy(
                        update={
                            "metadata": {
                                **page.metadata,
                                "content_origin": "official-api",
                                **({"canonical_url": canonical} if canonical else {}),
                            }
                        }
                    )
                    self._cache(url, page)
                    self._record(url, "official-api")
                    return page
                self._record(url, "official-api:too-short")

        # 3. HttpPageLoader（服务端渲染站点可直达）
        if self._http_loader is not None:
            try:
                page = await self._http_loader(url)
            except Exception as exc:  # noqa: BLE001
                self._record(url, f"http-failed:{type(exc).__name__}")
            else:
                if len(page.content.strip()) >= MIN_USEFUL_CONTENT_CHARS:
                    page = page.model_copy(
                        update={"metadata": {**page.metadata, "content_origin": "http"}}
                    )
                    self._cache(url, page)
                    self._record(url, "http")
                    return page
                self._record(url, "http:empty-page")

        # 4. aggregator jd_text 兜底（同样要求达到有效内容门槛——
        #    job-radar 的 jd_text 多为"标题+部门+城市"标题行，无 JD 信息，
        #    放进管线只会诱发 LLM 幻觉）
        if len(job.jd_text.strip()) >= MIN_USEFUL_CONTENT_CHARS:
            page = build_discovered_page(job)
            page = page.model_copy(
                update={
                    "metadata": {
                        **page.metadata,
                        "content_origin": page.metadata.get("content_origin")
                        or "jd_text-fallback",
                    }
                }
            )
            self._cache(url, page)
            self._record(url, "jd_text-fallback")
            return page
        if job.jd_text.strip():
            self._record(url, "jd_text:too-short")

        raise PageEmptyError(
            f"aggregator page has no usable content "
            f"(official API/http/jd_text all failed or below {MIN_USEFUL_CONTENT_CHARS} chars): {url}"
        )

    def _to_page(self, url: str, content: str, title: str, source: str) -> object:
        from web_task_agent.models import BrowserPage

        job = self._jobs_by_url.get(url)
        return BrowserPage(
            url=url,
            title=title or (job.title if job else ""),
            content=content,
            source=source,
            metadata=(
                {"tier": job.tier, "discovered_company": job.company} if job else {}
            ),
        )

    def _cache(self, url: str, page: object) -> None:
        if self._repository is not None:
            self._repository.cache_page(page)  # type: ignore[arg-type]

    def _record(self, url: str, strategy: str) -> None:
        self.resolution_log.append({"url": url, "strategy": strategy})
