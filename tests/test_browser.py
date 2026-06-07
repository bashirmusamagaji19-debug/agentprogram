import pytest

from tests.fixtures.job_pages import JOB_PAGES
from web_task_agent.browser import BrowserUseClient, FakeBrowserClient


@pytest.mark.asyncio
async def test_fake_browser_search_returns_one_matching_ai_page_when_target_count_is_one():
    browser = FakeBrowserClient(JOB_PAGES)

    results = await browser.search("AI LangGraph", target_count=1)

    assert len(results) == 1
    page = results[0]
    assert page.title == "AI Engineering Intern at Example AI"
    assert page.url == "https://example.com/jobs/ai-engineering-intern"
    assert "Python, LangGraph, and LLM" in page.content


@pytest.mark.asyncio
async def test_fake_browser_search_is_case_insensitive():
    browser = FakeBrowserClient(JOB_PAGES)

    results = await browser.search("fastapi sql", target_count=2)

    assert [page.url for page in results] == [
        "https://example.com/jobs/ml-platform-intern",
    ]


@pytest.mark.asyncio
async def test_fake_browser_search_with_target_count_zero_returns_empty_list():
    browser = FakeBrowserClient(JOB_PAGES)

    assert await browser.search("AI", target_count=0) == []


@pytest.mark.asyncio
async def test_fake_browser_open_url_returns_exact_page():
    browser = FakeBrowserClient(JOB_PAGES)

    page = await browser.open_url("https://example.com/jobs/ml-platform-intern")

    assert page.title == "ML Platform Intern at DataWorks"
    assert page.url == "https://example.com/jobs/ml-platform-intern"
    assert "Python, FastAPI, and SQL" in page.content


@pytest.mark.asyncio
async def test_fake_browser_open_url_missing_url_raises_value_error_with_url():
    browser = FakeBrowserClient(JOB_PAGES)
    missing_url = "https://example.com/jobs/missing"

    with pytest.raises(ValueError, match=missing_url):
        await browser.open_url(missing_url)


def test_browser_use_client_is_constructible_with_default_timeout():
    browser = BrowserUseClient()

    assert browser.default_timeout_seconds == 60


@pytest.mark.asyncio
async def test_browser_use_client_search_raises_not_implemented_error():
    browser = BrowserUseClient()

    with pytest.raises(NotImplementedError, match="deterministic MVP tests"):
        await browser.search("AI intern", target_count=1)


@pytest.mark.asyncio
async def test_browser_use_client_open_url_raises_not_implemented_error():
    browser = BrowserUseClient()

    with pytest.raises(NotImplementedError, match="deterministic MVP tests"):
        await browser.open_url("https://example.com/jobs/ai-engineering-intern")
