"""CachedPageLoader 与 page_cache 测试：缓存命中不发 HTTP（fake loader 计数）+ TTL 过期。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.fixtures.job_pages import JOB_PAGES
from web_task_agent.browser import CachedPageLoader, HttpPageLoader
from web_task_agent.models import BrowserPage
from web_task_agent.storage import JobRepository


class CountingFakeLoader:
    """记录调用次数的 fake loader，不发出任何 HTTP 请求。"""

    def __init__(self, pages: list[BrowserPage]) -> None:
        self._pages = {page.url: page for page in pages}
        self.call_count = 0

    async def __call__(self, url: str) -> BrowserPage:
        self.call_count += 1
        if url not in self._pages:
            raise KeyError(url)
        return self._pages[url]


@pytest.fixture
def repo(tmp_path: Path) -> JobRepository:
    repository = JobRepository(tmp_path / "cache.db")
    repository.initialize()
    return repository


@pytest.mark.asyncio
async def test_first_call_fetches_and_caches(repo: JobRepository):
    loader = CountingFakeLoader(JOB_PAGES)
    cached_loader = CachedPageLoader(loader, repo)
    target = JOB_PAGES[0].url

    page = await cached_loader(target)

    assert page.content == JOB_PAGES[0].content
    assert loader.call_count == 1
    assert repo.get_cached_page(target) is not None


@pytest.mark.asyncio
async def test_second_call_hits_cache_without_http(repo: JobRepository):
    loader = CountingFakeLoader(JOB_PAGES)
    cached_loader = CachedPageLoader(loader, repo)
    target = JOB_PAGES[0].url

    await cached_loader(target)
    await cached_loader(target)
    await cached_loader(target)

    assert loader.call_count == 1  # 后两次全部命中缓存


@pytest.mark.asyncio
async def test_expired_cache_is_refetched(repo: JobRepository):
    loader = CountingFakeLoader(JOB_PAGES)
    cached_loader = CachedPageLoader(loader, repo, max_age_hours=24.0)
    target = JOB_PAGES[0].url

    await cached_loader(target)
    # 把 fetched_at 回拨到 25 小时前，模拟 TTL 过期
    stale = repo.get_cached_page(target)
    assert stale is not None
    with repo._connection() as conn:
        conn.execute(
            "UPDATE page_cache SET fetched_at = ? WHERE url = ?",
            ((datetime.now(UTC) - timedelta(hours=25)).isoformat(), target),
        )

    assert repo.get_cached_page(target) is None  # 过期不可见
    await cached_loader(target)
    assert loader.call_count == 2  # 回源重抓


@pytest.mark.asyncio
async def test_missing_url_still_raises_after_cache_miss(repo: JobRepository):
    loader = CountingFakeLoader(JOB_PAGES)
    cached_loader = CachedPageLoader(loader, repo)

    with pytest.raises(KeyError):
        await cached_loader("https://example.com/not-in-fixture")


def test_repository_page_cache_roundtrip(tmp_path: Path):
    repository = JobRepository(tmp_path / "direct.db")
    repository.initialize()
    page = BrowserPage(url="https://example.com/x", title="T", content="C", source="s")

    assert repository.get_cached_page("https://example.com/x") is None
    repository.cache_page(page)
    cached = repository.get_cached_page("https://example.com/x")
    assert cached is not None
    assert cached.content == "C"
    assert cached.source == "s"


def test_http_page_loader_satisfies_cached_wrapper_signature():
    """CachedPageLoader 期望的 loader 签名与 HttpPageLoader 兼容。"""
    loader = HttpPageLoader(timeout_seconds=5)
    assert callable(loader)
