from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Protocol

import httpx
from pydantic import ValidationError

from .models import SearchCandidate


class SearchProviderError(RuntimeError):
    pass


class SearchProviderConfigurationError(SearchProviderError):
    pass


class SearchProvider(Protocol):
    async def search(self, query: str, limit: int = 10) -> list[SearchCandidate]: ...


class FixtureSearchProvider:
    def __init__(self, fixtures: Sequence[SearchCandidate]) -> None:
        self.fixtures = list(fixtures)

    async def search(self, query: str, limit: int = 10) -> list[SearchCandidate]:
        lowered = query.casefold()
        tokens = [token for token in lowered.replace("，", " ").split() if token]
        matches = [
            item
            for item in self.fixtures
            if not tokens
            or any(token in f"{item.title} {item.snippet}".casefold() for token in tokens)
        ]
        if not matches and self.fixtures:
            matches = self.fixtures
        return matches[: max(0, min(limit, 20))]


class TavilySearchProvider:
    endpoint = "https://api.tavily.com/search"

    def __init__(self, api_key: str, *, client: httpx.AsyncClient | None = None) -> None:
        self.api_key = api_key
        self._client = client
        self.last_malformed_count = 0

    @classmethod
    def from_environment(cls) -> TavilySearchProvider:
        key = os.getenv("TAVILY_API_KEY", "").strip()
        if not key:
            raise SearchProviderConfigurationError("TAVILY_API_KEY is required for online search")
        return cls(key)

    async def search(self, query: str, limit: int = 10) -> list[SearchCandidate]:
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30)
        try:
            response = await client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": max(1, min(limit, 20)),
                },
            )
            if response.status_code >= 400:
                raise SearchProviderError(f"tavily returned HTTP {response.status_code}")
            payload = response.json()
            results = payload.get("results", [])
            candidates: list[SearchCandidate] = []
            malformed_count = 0
            for item in results[:limit]:
                try:
                    candidates.append(
                        SearchCandidate(
                            url=item.get("url", ""),
                            title=item.get("title", ""),
                            snippet=item.get("content", ""),
                            source="tavily",
                        )
                    )
                except (AttributeError, TypeError, ValidationError):
                    malformed_count += 1
                    continue
            self.last_malformed_count = malformed_count
            return candidates
        except httpx.HTTPError as exc:
            raise SearchProviderError(f"tavily request failed: {type(exc).__name__}") from exc
        finally:
            if own_client:
                await client.aclose()
