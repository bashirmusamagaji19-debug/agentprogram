from __future__ import annotations

from collections.abc import Awaitable, Callable
import inspect
import re
from typing import Any, Protocol
from urllib.parse import quote_plus
from urllib import request
from html.parser import HTMLParser

from web_task_agent.models import BrowserPage


PageLoader = Callable[[str], Awaitable[BrowserPage]]
SessionFactory = Callable[[], Any]


class BrowserConfigurationError(RuntimeError):
    """Raised when the real browser adapter cannot run a requested action."""


class BrowserClient(Protocol):
    async def search(self, query: str, target_count: int) -> list[BrowserPage]:
        """Search for pages matching a query."""

    async def open_url(self, url: str) -> BrowserPage:
        """Open a page by exact URL."""


class FakeBrowserClient:
    def __init__(self, pages: list[BrowserPage]) -> None:
        self._pages = list(pages)

    async def search(self, query: str, target_count: int) -> list[BrowserPage]:
        if target_count <= 0:
            return []

        terms = query.casefold().split()
        scored_pages: list[tuple[int, int, BrowserPage]] = []
        for index, page in enumerate(self._pages):
            searchable_text = f"{page.title} {page.content}".casefold()
            score = sum(searchable_text.count(term) for term in terms)
            if score > 0:
                scored_pages.append((score, index, page))

        scored_pages.sort(key=lambda item: (-item[0], item[1]))
        return [page for _, _, page in scored_pages[:target_count]]

    async def open_url(self, url: str) -> BrowserPage:
        for page in self._pages:
            if page.url == url:
                return page
        raise ValueError(f"Browser page not found for URL: {url}")


class BrowserUseClient:
    def __init__(
        self,
        default_timeout_seconds: int = 60,
        page_loader: PageLoader | None = None,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self.default_timeout_seconds = default_timeout_seconds
        self._page_loader = page_loader
        self._session_factory = session_factory

    async def search(self, query: str, target_count: int) -> list[BrowserPage]:
        if target_count <= 0:
            return []

        search_url = f"https://www.google.com/search?q={quote_plus(query)}"
        return [await self.open_url(search_url)]

    async def open_url(self, url: str) -> BrowserPage:
        try:
            if self._page_loader is not None:
                return await self._page_loader(url)
            return await self._load_with_browser_use(url)
        except BrowserConfigurationError:
            raise
        except Exception as exc:
            raise BrowserConfigurationError(
                f"browser-use failed while opening {url}: {exc}"
            ) from exc

    async def _load_with_browser_use(self, url: str) -> BrowserPage:
        session = self._create_session()
        try:
            await session.start()
            page = await session.new_page(url)
            title = await self._read_title(page)
            content = await self._read_body_text(page)
            return BrowserPage(
                url=url,
                title=title,
                content=content,
                source="browser-use",
                metadata={"timeout_seconds": self.default_timeout_seconds},
            )
        except Exception as exc:
            raise BrowserConfigurationError(
                f"browser-use failed while opening {url}: {exc}"
            ) from exc
        finally:
            await session.close()

    def _create_session(self) -> Any:
        if self._session_factory is not None:
            return self._session_factory()

        try:
            from browser_use import BrowserSession
        except ImportError as exc:
            raise BrowserConfigurationError(
                "browser-use is not installed in the active environment. "
                "Install project dependencies inside .venv or re-run with --demo."
            ) from exc

        return BrowserSession(
            headless=True,
            is_local=True,
            cloud_browser=False,
            use_cloud=False,
        )

    async def _read_title(self, page: Any) -> str:
        if hasattr(page, "get_title"):
            return str(await self._call(page.get_title))
        if hasattr(page, "title"):
            return str(await self._call(page.title))
        return ""

    async def _read_body_text(self, page: Any) -> str:
        if hasattr(page, "inner_text"):
            return str(await self._call(page.inner_text, "body"))
        if hasattr(page, "evaluate"):
            try:
                text = await self._call(
                    page.evaluate,
                    "() => document.body ? document.body.innerText : ''",
                )
                if str(text).strip():
                    return str(text)
            except Exception:
                pass
            return str(
                await self._call(
                    page.evaluate,
                    "() => document.documentElement ? document.documentElement.innerText : ''",
                )
            )
        return ""

    async def _call(self, func: Any, *args: Any) -> Any:
        result = func(*args)
        if inspect.isawaitable(result):
            return await result
        return result


class HttpPageLoader:
    def __init__(self, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds

    async def __call__(self, url: str) -> BrowserPage:
        return await self._load(url)

    async def _load(self, url: str) -> BrowserPage:
        return self._load_sync(url)

    def _load_sync(self, url: str) -> BrowserPage:
        req = request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0 Safari/537.36"
                ),
            },
        )
        with request.urlopen(req, timeout=self.timeout_seconds) as response:
            html = response.read().decode("utf-8", errors="replace")
        return BrowserPage(
            url=url,
            title=self._extract_title(html),
            content=self._extract_text(html),
            source="http",
            metadata={"timeout_seconds": self.timeout_seconds},
        )

    def _extract_title(self, html: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if not match:
            return ""
        return self._clean_text(match.group(1))

    def _extract_text(self, html: str) -> str:
        parser = _TextExtractor()
        parser.feed(html)
        return parser.get_text()

    def _clean_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._chunks.append(text)

    def get_text(self) -> str:
        return "\n".join(self._chunks)
