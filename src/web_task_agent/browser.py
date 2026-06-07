from __future__ import annotations

from typing import Protocol

from web_task_agent.models import BrowserPage


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
    def __init__(self, default_timeout_seconds: int = 60) -> None:
        self.default_timeout_seconds = default_timeout_seconds

    async def search(self, query: str, target_count: int) -> list[BrowserPage]:
        raise NotImplementedError(
            "Real browser-use integration comes after deterministic MVP tests."
        )

    async def open_url(self, url: str) -> BrowserPage:
        raise NotImplementedError(
            "Real browser-use integration comes after deterministic MVP tests."
        )
