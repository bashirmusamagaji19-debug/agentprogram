from __future__ import annotations

from collections.abc import Awaitable, Callable
import inspect
from typing import Any, Protocol
from urllib.parse import quote_plus

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
            return str(
                await self._call(
                    page.evaluate,
                    "() => document.body ? document.body.innerText : ''",
                )
            )
        return ""

    async def _call(self, func: Any, *args: Any) -> Any:
        result = func(*args)
        if inspect.isawaitable(result):
            return await result
        return result
