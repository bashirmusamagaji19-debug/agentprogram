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

AI_TITLE_KEYWORDS = (
    "AI",
    "算法",
    "大模型",
    "大语言模型",
    "LLM",
    "机器学习",
    "深度学习",
    "NLP",
    "多模态",
    "Agent",
    "数据挖掘",
    "推荐",
    "CV",
    "视觉",
    "RAG",
)

INTERN_TITLE_KEYWORDS = ("实习",)


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
