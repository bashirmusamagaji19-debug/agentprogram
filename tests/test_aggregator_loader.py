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
            # 正文必须过 120 字符门槛（#27 起官方 API 路径与 http/jd_text 同门槛）
            content = "岗位职责：官方API正文，长度需要超过一百二十个字符的有效内容门槛，用于确认解析链命中官方接口路径，这里持续填充内容确保长度达标，保证测试稳定不抖动不误报。" * 2
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
            jd_text="岗位职责：jd兜底正文，需要有足够的长度才能通过有效内容门槛（120 字符），这里持续填充长度直到超过门槛线为止，确保测试稳定。" * 2,
        ),
        DiscoveredJob(
            url="https://b.example.com/job/2",
            title="算法实习生",
            company="示例B",
            jd_text="岗位职责：B站兜底正文。" + "内容填充。"*25,
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
async def test_title_only_jd_text_rejected_as_fallback():
    """标题行式 jd_text（job-radar 实测中位数 46 字符）不得进管线——诱发 LLM 幻觉（#21）。"""
    from web_task_agent.browser import PageEmptyError

    title_only = DiscoveredJob(
        url="https://c.example.com/job/4",
        title="混元多模态研究（实习生 青云计划）",
        jd_text="混元多模态-大模型数据挖掘 · 实习生 青云计划 · TEG · 深圳总部",
    )
    loader = AggregatorPageLoader([title_only], official_api=None, http_loader=None)

    with pytest.raises(PageEmptyError):
        await loader(title_only.url)
    assert loader.resolution_log[-1]["strategy"] == "jd_text:too-short"


@pytest.mark.asyncio
async def test_boilerplate_http_page_below_threshold_rejected():
    """98 字符的浏览器兼容提示（第三方站 SPA 壳）不得当有效内容（#21）。"""
    from web_task_agent.browser import PageEmptyError

    job = DiscoveredJob(
        url="https://c.example.com/job/5",
        title="算法实习生",
        jd_text="岗位职责：兜底正文，长度足够通过有效内容门槛检查，继续填充内容直到超过一百二十个字符的门槛线，保证测试稳定不抖动。" * 3,
    )
    boilerplate = "【温馨提示】检测到您正在使用兼容模式/旧版IE浏览器，功能可能无法正常使用。\nChrome | Firefox | Edge"
    http = FakeHttpLoader(pages={job.url: boilerplate})
    loader = AggregatorPageLoader([job], official_api=None, http_loader=http)

    page = await loader(job.url)

    # boilerplate 不够门槛 → 落到够长的 jd_text 兜底
    assert loader.resolution_log[-2]["strategy"] == "http:empty-page"
    assert page.source.startswith("aggregator:")


@pytest.mark.asyncio
async def test_non_aggregator_url_delegates_to_http_loader(jobs):
    http = FakeHttpLoader(pages={"https://other.example.com/x": "非聚合URL正文" * 20})
    loader = AggregatorPageLoader(jobs, official_api=None, http_loader=http)

    page = await loader("https://other.example.com/x")

    assert page.content == "非聚合URL正文" * 20


# ── 旧壳页缓存绕过内容门槛（#25）─────────────────────────────────────


@pytest.mark.asyncio
async def test_stale_shell_page_cache_is_ignored(jobs, repo):
    """门槛收紧前写入的旧壳页（<120 字符）在 TTL 内命中也不得当作有效内容（#25）。"""
    from web_task_agent.browser import CachedPageLoader, HttpPageLoader
    from web_task_agent.official_api import OfficialApiUnavailableError

    shell_page = BrowserPage(
        url=jobs[0].url, title="壳", content="热搜", source="browser"
    )
    repo.cache_page(shell_page)  # 模拟旧版本写入的 11 字符壳页缓存

    official = FakeOfficialApi()
    official.fail_for = {jobs[0].url}
    official_fetches: list[str] = []

    async def failing_official(url: str):  # noqa: ANN202
        official_fetches.append(url)
        raise OfficialApiUnavailableError(f"unavailable: {url}")

    long_page = "HTTP真实正文，" * 40
    http = FakeHttpLoader(pages={jobs[0].url: long_page})
    loader = AggregatorPageLoader(
        jobs,
        official_api=official,
        http_loader=CachedPageLoader(HttpPageLoader(), repo),
        repository=repo,
    )
    # 把官方 API 换成会失败的 fake，强制落到 HTTP 路径
    loader._official_api = failing_official  # noqa: SLF001
    loader._http_loader = CachedPageLoader(http, repo)  # noqa: SLF001

    page = await loader(jobs[0].url)

    # 旧壳页缓存被无视，重新抓取拿到长正文
    assert page.content == long_page
    assert loader.resolution_log[-1]["strategy"] == "http"
    # 抓到的有效页面覆盖写回缓存
    cached = repo.get_cached_page(jobs[0].url, max_age_hours=24)
    assert cached is not None and len(cached.content) >= 120


@pytest.mark.asyncio
async def test_cachedpageloader_ignores_below_threshold_cache(jobs, repo):
    """CachedPageLoader 自身也要过门槛：短缓存不命中，回源后覆盖缓存。"""
    from web_task_agent.browser import CachedPageLoader

    shell = BrowserPage(url=jobs[0].url, title="壳", content="热搜", source="browser")
    repo.cache_page(shell)

    real = BrowserPage(url=jobs[0].url, title="真", content="正文" * 100, source="browser")

    class CountingLoader:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def __call__(self, url: str) -> BrowserPage:
            self.calls.append(url)
            return real

    inner = CountingLoader()
    loader = CachedPageLoader(inner, repo)

    page = await loader(jobs[0].url)

    assert page is real
    assert inner.calls == [jobs[0].url]  # 短缓存未挡住回源
    # 第二次调用：有效缓存命中，不回源
    await loader(jobs[0].url)
    assert inner.calls == [jobs[0].url]


@pytest.mark.asyncio
async def test_official_api_short_content_falls_through_without_caching(jobs, repo):
    """官方 API 短正文（<120 字符）不得进管线也不得写缓存，落后续兜底（#27）。"""
    class ShortOfficialApi:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def fetch(self, url: str):  # noqa: ANN201
            self.calls.append(url)

            class _Content:
                content = "公司：腾讯\n工作地点：深圳"  # 15 字符壳
                title = "壳岗"
                company = "腾讯"

            return _Content()

    official = ShortOfficialApi()
    loader = AggregatorPageLoader(
        jobs, official_api=official, http_loader=None, repository=repo
    )

    page = await loader(jobs[0].url)

    # 短正文被门槛挡下 → 落到够长的 jd_text 兜底
    assert page.source.startswith("aggregator:")
    assert loader.resolution_log[-2]["strategy"] == "official-api:too-short"
    # 缓存里不得是 official-api 的短壳——否则该 URL 缓存永远失效、
    # 每次运行重调 API（#27）；有效 jd_text 兜底页允许写缓存
    cached = repo.get_cached_page(jobs[0].url, max_age_hours=24)
    assert cached is not None and "公司：腾讯" not in cached.content


@pytest.mark.asyncio
async def test_official_api_canonical_url_travels_through_metadata_and_cache(jobs, repo):
    """官方 API 返回 canonical_url（如腾讯校招 join.qq.com 页）时：
    - BrowserPage.url 保持发现 URL（缓存键/诊断语义不变）
    - canonical 经 metadata 透传，缓存 TTL 内的重复运行不丢失
    """
    canonical_url = "https://join.qq.com/post_detail.html?postId=X"

    class _CanonicalOfficialApi(FakeOfficialApi):
        async def fetch(self, url):  # noqa: ANN001, ANN201
            content = await super().fetch(url)
            content.canonical_url = canonical_url
            return content

    loader = AggregatorPageLoader(
        jobs, official_api=_CanonicalOfficialApi(), http_loader=FakeHttpLoader(), repository=repo
    )

    page = await loader(jobs[0].url)

    assert page.url == jobs[0].url
    assert page.metadata["canonical_url"] == canonical_url
    assert loader.resolution_log[-1]["url"] == jobs[0].url
    assert loader.resolution_log[-1]["strategy"] == "official-api"

    # 缓存命中路径：canonical 仍在（不再调 API）
    second = await loader(jobs[0].url)
    assert second.metadata["canonical_url"] == canonical_url
