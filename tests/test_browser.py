import pytest
import browser_use
from pathlib import Path

from tests.fixtures.job_pages import JOB_PAGES
from web_task_agent import browser as browser_module
from web_task_agent.browser import BrowserUseClient, FakeBrowserClient, HttpPageLoader
from web_task_agent.models import BrowserPage


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


def test_browser_use_client_default_session_uses_local_browser(monkeypatch):
    captured_kwargs: dict[str, object] = {}

    class FakeSession:
        def __init__(self, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(browser_use, "BrowserSession", FakeSession)

    session = BrowserUseClient()._create_session()

    assert isinstance(session, FakeSession)
    assert "timeout" not in captured_kwargs
    assert captured_kwargs["headless"] is True
    assert captured_kwargs["use_cloud"] is False
    assert captured_kwargs["cloud_browser"] is False
    assert captured_kwargs["is_local"] is True


@pytest.mark.asyncio
async def test_browser_use_client_search_loads_search_url_with_injected_loader():
    opened_urls: list[str] = []

    async def loader(url: str) -> BrowserPage:
        opened_urls.append(url)
        return BrowserPage(
            url=url,
            title="Search results",
            content="AI intern LangGraph browser-use result page",
            source="browser-use",
        )

    browser = BrowserUseClient(page_loader=loader)

    pages = await browser.search("AI intern", target_count=2)

    assert len(pages) == 1
    assert pages[0].source == "browser-use"
    assert opened_urls == [
        "https://www.google.com/search?q=AI+intern",
    ]


@pytest.mark.asyncio
async def test_browser_use_client_open_url_uses_injected_loader():
    async def loader(url: str) -> BrowserPage:
        return BrowserPage(
            url=url,
            title="AI Engineering Intern",
            content="Company: Example AI\nSkills: Python, LangGraph",
            source="browser-use",
        )

    browser = BrowserUseClient(page_loader=loader)

    page = await browser.open_url("https://example.com/jobs/ai-engineering-intern")

    assert page.title == "AI Engineering Intern"
    assert page.source == "browser-use"


@pytest.mark.asyncio
async def test_browser_use_client_wraps_loader_failures_with_url():
    async def loader(url: str) -> BrowserPage:
        raise RuntimeError("navigation failed")

    browser = BrowserUseClient(page_loader=loader)

    with pytest.raises(Exception, match="https://example.com/jobs/missing") as excinfo:
        await browser.open_url("https://example.com/jobs/missing")

    assert excinfo.type is browser_module.BrowserConfigurationError


@pytest.mark.asyncio
async def test_browser_use_client_loads_page_with_browser_session_factory():
    events: list[str] = []

    class FakePage:
        async def title(self) -> str:
            return "AI Engineering Intern"

        async def inner_text(self, selector: str) -> str:
            events.append(f"inner_text:{selector}")
            return "Company: Example AI\nSkills: Python, browser-use"

    class FakeSession:
        async def start(self) -> None:
            events.append("start")

        async def new_page(self, url: str) -> FakePage:
            events.append(f"new_page:{url}")
            return FakePage()

        async def close(self) -> None:
            events.append("close")

    browser = BrowserUseClient(session_factory=FakeSession)

    page = await browser.open_url("https://example.com/jobs/ai-engineering-intern")

    assert page == BrowserPage(
        url="https://example.com/jobs/ai-engineering-intern",
        title="AI Engineering Intern",
        content="Company: Example AI\nSkills: Python, browser-use",
        source="browser-use",
        metadata={"timeout_seconds": 60},
    )
    assert events == [
        "start",
        "new_page:https://example.com/jobs/ai-engineering-intern",
        "inner_text:body",
        "close",
    ]


@pytest.mark.asyncio
async def test_browser_use_client_loads_browser_use_page_api():
    events: list[str] = []

    class FakeBrowserUsePage:
        async def get_title(self) -> str:
            return "Browser-use Search Results"

        async def evaluate(self, page_function: str) -> str:
            events.append(page_function)
            return "AI intern result from browser-use Page API"

    class FakeSession:
        async def start(self) -> None:
            pass

        async def new_page(self, url: str) -> FakeBrowserUsePage:
            return FakeBrowserUsePage()

        async def close(self) -> None:
            pass

    browser = BrowserUseClient(session_factory=FakeSession)

    page = await browser.open_url("https://example.com/search")

    assert page.title == "Browser-use Search Results"
    assert page.content == "AI intern result from browser-use Page API"
    assert events == ["() => document.body ? document.body.innerText : ''"]


@pytest.mark.asyncio
async def test_browser_use_client_loads_page_title_and_body_from_browser_session():
    events: list[str] = []

    class FakePage:
        async def get_title(self) -> str:
            return "AI Deployment Engineer"

        async def evaluate(self, page_function: str) -> str:
            events.append(page_function)
            return "Company: OpenAI\nRequirements: Python, LLM"

    class FakeSession:
        async def start(self) -> None:
            pass

        async def new_page(self, url: str) -> FakePage:
            return FakePage()

        async def close(self) -> None:
            pass

    browser = BrowserUseClient(session_factory=FakeSession)

    page = await browser.open_url("https://openai.com/careers/test")

    assert page.title == "AI Deployment Engineer"
    assert page.content == "Company: OpenAI\nRequirements: Python, LLM"
    assert events == ["() => document.body ? document.body.innerText : ''"]


@pytest.mark.asyncio
async def test_browser_use_client_closes_session_when_page_loading_fails():
    events: list[str] = []

    class FailingSession:
        async def start(self) -> None:
            events.append("start")

        async def new_page(self, url: str) -> object:
            events.append(f"new_page:{url}")
            raise RuntimeError("blocked")

        async def close(self) -> None:
            events.append("close")

    browser = BrowserUseClient(session_factory=FailingSession)

    with pytest.raises(Exception, match="blocked") as excinfo:
        await browser.open_url("https://example.com/jobs/blocked")

    assert excinfo.type is browser_module.BrowserConfigurationError
    assert events == ["start", "new_page:https://example.com/jobs/blocked", "close"]


@pytest.mark.asyncio
async def test_browser_use_client_can_load_real_http_page_via_page_loader():
    async def loader(url: str) -> BrowserPage:
        html = """
        <html>
          <head><title>AI Deployment Engineer</title></head>
          <body>
            <h1>AI Deployment Engineer</h1>
            <p>Company: OpenAI</p>
            <p>Requirements: Python, LLM</p>
          </body>
        </html>
        """
        return BrowserPage(
            url=url,
            title="AI Deployment Engineer",
            content=html,
            source="http",
        )

    browser = BrowserUseClient(page_loader=loader)

    page = await browser.open_url("https://openai.com/careers/test")

    assert page.title == "AI Deployment Engineer"
    assert "Python" in page.content


@pytest.mark.asyncio
async def test_http_page_loader_extracts_title_and_text():
    html = """
    <html>
      <head><title>AI Deployment Engineer</title></head>
      <body>
        <h1>AI Deployment Engineer</h1>
        <p>Company: OpenAI</p>
        <p>Requirements: Python, LLM</p>
        <script>window.x = 1;</script>
      </body>
    </html>
    """

    def fake_urlopen(req, timeout=0):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return html.encode("utf-8")

        return Response()

    loader = HttpPageLoader()
    original = browser_module.request.urlopen
    browser_module.request.urlopen = fake_urlopen
    try:
        page = await loader("https://openai.com/careers/test")
    finally:
        browser_module.request.urlopen = original

    assert page.title == "AI Deployment Engineer"
    assert page.source == "http"
    assert "Company: OpenAI" in page.content


def test_http_loader_raises_page_timeout_error_on_connection_failure():
    import sys, socket
    from web_task_agent.browser import HttpPageLoader, PageTimeoutError
    from urllib.error import URLError

    browser_module = sys.modules["web_task_agent.browser"]
    loader = HttpPageLoader(timeout_seconds=1)
    original = browser_module.request.urlopen

    def fake_urlopen(req, timeout=None):
        raise URLError(socket.timeout("timed out"))

    browser_module.request.urlopen = fake_urlopen
    try:
        import asyncio
        with pytest.raises(PageTimeoutError) as exc:
            asyncio.run(loader("https://example.com/timeout"))
        assert "timed out" in str(exc.value)
    finally:
        browser_module.request.urlopen = original


def test_http_loader_raises_page_http_error_on_404():
    import sys
    from web_task_agent.browser import HttpPageLoader, PageHttpError
    from urllib.error import HTTPError
    from io import BytesIO

    browser_module = sys.modules["web_task_agent.browser"]
    loader = HttpPageLoader(timeout_seconds=1)
    original = browser_module.request.urlopen

    def fake_urlopen(req, timeout=None):
        raise HTTPError("https://example.com/404", 404, "Not Found", {}, BytesIO(b""))

    browser_module.request.urlopen = fake_urlopen
    try:
        import asyncio
        with pytest.raises(PageHttpError) as exc:
            asyncio.run(loader("https://example.com/404"))
        assert exc.value.status_code == 404
    finally:
        browser_module.request.urlopen = original


def test_http_loader_raises_page_empty_error_on_js_shell():
    import sys
    from web_task_agent.browser import HttpPageLoader, PageEmptyError

    browser_module = sys.modules["web_task_agent.browser"]
    loader = HttpPageLoader(timeout_seconds=1)
    original = browser_module.request.urlopen

    def fake_urlopen(req, timeout=None):
        import io
        return io.BytesIO(b"<html><body><script>window.x=1;</script></body></html>")

    browser_module.request.urlopen = fake_urlopen
    try:
        import asyncio
        with pytest.raises(PageEmptyError) as exc:
            asyncio.run(loader("https://example.com/js-only"))
        assert "empty content" in str(exc.value)
    finally:
        browser_module.request.urlopen = original
