"""AggregatorPageLoader 测试：内容解析链的顺序、兜底与缓存行为（全 fake，无真实 HTTP）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from web_task_agent.job_sources import (
    AggregatorPageLoader,
    DiscoveredJob,
)
from web_task_agent.models import BrowserPage
from web_task_agent.storage import JobRepository


class FakeOfficialApi:
    def __init__(self, *, fail_for: set[str] | None = None) -> None:
        self.fail_for = fail_for or set()
        self.calls: list[str] = []

    async def fetch(self, url: str):  # noqa: ANN201
        self.calls.append(url)
        if url in self.fail_for:
            from web_task_agent.official_api import OfficialApiUnavailableError

            raise OfficialApiUnavailableError(f"unavailable: {url}")

        class _Content:
            content = "岗位职责：官方API正文，长度超过五十个字符阈值，用于确认解析链命中官方接口路径。" * 2
            title = "官方API标题"
            company = "官方公司"

        return _Content()


class FakeHttpLoader:
    def __init__(self, pages: dict[str, str] | None = None) -> None:
        self.pages = pages or {}
        self.calls: list[str] = []

    async def __call__(self, url: str) -> BrowserPage:
        self.calls.append(url)
        if url not in self.pages:
            raise RuntimeError("simulated network failure")
        return BrowserPage(url=url, title="HTTP标题", content=self.pages[url], source="browser")


@pytest.fixture
def jobs() -> list[DiscoveredJob]:
    return [
        DiscoveredJob(
            url="https://a.example.com/job/1",
            title="大模型算法实习生",
            company="示例A",
            jd_text="岗位职责：jd兜底正文，同样需要超过五十个字符的长度阈值才能被判定为有效内容。" * 2,
        ),
        DiscoveredJob(
            url="https://b.example.com/job/2",
            title="算法实习生",
            company="示例B",
            jd_text="岗位职责：B站兜底正文。" + "内容填充。"*20,
        ),
    ]


@pytest.fixture
def repo(tmp_path: Path) -> JobRepository:
    repository = JobRepository(tmp_path / "loader.db")
    repository.initialize()
    return repository


@pytest.mark.asyncio
async def test_official_api_wins_over_http_and_jd_text(jobs, repo):
    official = FakeOfficialApi()
    http = FakeHttpLoader(pages={jobs[0].url: "HTTP正文" * 50})
    loader = AggregatorPageLoader(
        jobs, official_api=official, http_loader=http, repository=repo
    )

    page = await loader(jobs[0].url)

    assert "官方API正文" in page.content
    assert page.source == "official-api"
    assert official.calls == [jobs[0].url]
    assert http.calls == []  # 官方 API 命中后不再走 HTTP
    assert loader.resolution_log[-1]["strategy"] == "official-api"


@pytest.mark.asyncio
async def test_unsupported_api_domain_falls_to_http_then_jd_text(jobs, repo):
    # official_api 为 None（未配置任何适配器）→ 直接走 HTTP
    http = FakeHttpLoader()  # 网络失败
    loader = AggregatorPageLoader(jobs, official_api=None, http_loader=http, repository=repo)

    page = await loader(jobs[0].url)

    assert page.source.startswith("aggregator:")
    assert "jd兜底正文" in page.content
    assert loader.resolution_log[-1]["strategy"] == "jd_text-fallback"


@pytest.mark.asyncio
async def test_http_empty_page_falls_to_jd_text(jobs, repo):
    http = FakeHttpLoader(pages={jobs[0].url: "壳"})  # SPA 空壳 <50 字符
    loader = AggregatorPageLoader(jobs, official_api=None, http_loader=http, repository=repo)

    page = await loader(jobs[0].url)

    assert page.source.startswith("aggregator:")
    assert loader.resolution_log[-2]["strategy"] == "http:empty-page"
    assert loader.resolution_log[-1]["strategy"] == "jd_text-fallback"


@pytest.mark.asyncio
async def test_server_rendered_http_page_wins_over_jd_text(jobs, repo):
    long_content = "服务端渲染的完整正文，" * 30
    http = FakeHttpLoader(pages={jobs[0].url: long_content})
    loader = AggregatorPageLoader(jobs, official_api=None, http_loader=http, repository=repo)

    page = await loader(jobs[0].url)

    assert page.content == long_content
    assert page.source == "browser"
    assert loader.resolution_log[-1]["strategy"] == "http"


@pytest.mark.asyncio
async def test_second_call_hits_cache(jobs, repo):
    loader = AggregatorPageLoader(jobs, official_api=None, http_loader=None, repository=repo)

    await loader(jobs[0].url)
    await loader(jobs[0].url)

    strategies = [r["strategy"] for r in loader.resolution_log]
    assert strategies.count("cache") == 1
    assert strategies.count("jd_text-fallback") == 1


@pytest.mark.asyncio
async def test_all_sources_exhausted_raises_page_empty(jobs):
    job_no_jd = DiscoveredJob(url="https://c.example.com/job/3", title="x", jd_text="")
    loader = AggregatorPageLoader([job_no_jd], official_api=None, http_loader=None)

    from web_task_agent.browser import PageEmptyError

    with pytest.raises(PageEmptyError):
        await loader(job_no_jd.url)


@pytest.mark.asyncio
async def test_non_aggregator_url_delegates_to_http_loader(jobs):
    http = FakeHttpLoader(pages={"https://other.example.com/x": "非聚合URL正文" * 20})
    loader = AggregatorPageLoader(jobs, official_api=None, http_loader=http)

    page = await loader("https://other.example.com/x")

    assert page.content == "非聚合URL正文" * 20
