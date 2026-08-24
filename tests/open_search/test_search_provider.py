import pytest

from web_task_agent.open_search.models import SearchCandidate
from web_task_agent.open_search.search_provider import (
    FixtureSearchProvider,
    SearchProviderConfigurationError,
    SearchProviderError,
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


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def post(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        return self.response


@pytest.mark.asyncio
async def test_tavily_provider_maps_results_and_bounds_limit():
    client = _Client(
        _Response(
            200,
            {
                "results": [
                    {
                        "url": "https://jobs.example.com/careers/1",
                        "title": "Agent Intern",
                        "content": "Python",
                    }
                ]
            },
        )
    )
    result = await TavilySearchProvider("key", client=client).search("Agent", limit=1)
    assert result[0].title == "Agent Intern"
    assert result[0].snippet == "Python"
    assert client.calls[0][1]["json"]["max_results"] == 1


@pytest.mark.asyncio
async def test_tavily_provider_classifies_http_failure():
    with pytest.raises(SearchProviderError, match="HTTP 429"):
        await TavilySearchProvider("key", client=_Client(_Response(429, {}))).search("Agent")
