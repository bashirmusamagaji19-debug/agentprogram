import pytest

from web_task_agent.open_search.models import SearchCandidate
from web_task_agent.open_search.search_provider import (
    FixtureSearchProvider,
    SearchProviderConfigurationError,
    TavilySearchProvider,
)


def test_tavily_provider_requires_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(SearchProviderConfigurationError):
        TavilySearchProvider.from_environment()


@pytest.mark.asyncio
async def test_fixture_provider_returns_bounded_candidates():
    candidate = SearchCandidate(url="https://example.com/jobs/1", title="Agent 北京")
    result = await FixtureSearchProvider(fixtures=[candidate]).search("Agent 北京")
    assert len(result) == 1
    assert result[0].url.startswith("https://")
