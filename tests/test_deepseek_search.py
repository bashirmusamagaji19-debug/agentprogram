import json

import pytest

from web_task_agent.browser import PageEmptyError, PageHttpError
from web_task_agent.models import BrowserPage


def test_search_returns_links_without_promoting_summary_to_jd():
    from web_task_agent.deepseek_search import DeepSeekWebClient

    sent = []

    def transport(endpoint, key, payload, timeout):
        sent.append((endpoint, payload))
        return {"results": [{"title": "AI 实习", "url": "https://jobs.example.org/jobs/1",
                             "summary": "Invented city and requirements", "content": ""}]}

    client = DeepSeekWebClient(api_key="secret", transport=transport)
    jobs = client.search("上海 AI 实习", count=3)
    assert jobs[0].jd_text == ""
    assert jobs[0].location == ""
    assert jobs[0].source == "deepseek-search"
    assert sent[0][1] == {"query": "上海 AI 实习", "count": 3, "need_content": False}
    assert client.usage["search"]["billable"] == 1
    assert "secret" not in json.dumps(client.usage)


def test_empty_search_and_empty_fetch_are_not_billable():
    from web_task_agent.deepseek_search import DeepSeekWebClient

    client = DeepSeekWebClient(api_key="secret", transport=lambda *args: {"results": []})
    assert client.search("AI") == []
    assert client.usage["search"]["billable"] == 0
    with pytest.raises(PageEmptyError):
        client.fetch("https://example.org/jobs/1")
    assert client.usage["fetch"]["billable"] == 0


@pytest.mark.asyncio
async def test_fetch_recovers_empty_page_but_does_not_claim_official_verification():
    from web_task_agent.deepseek_search import DeepSeekWebClient, FetchFallbackLoader

    async def empty(url):
        raise PageEmptyError("empty")

    client = DeepSeekWebClient(api_key="secret", transport=lambda *args: {
        "title": "AI 实习", "content": "# 任职要求\n熟悉 Python 和大模型。" * 20,
    })
    page = await FetchFallbackLoader(empty, client, max_calls=1)("https://example.org/jobs/1")
    assert page.source == "deepseek-fetch"
    assert page.metadata["content_origin"] == "deepseek-fetch"
    assert "api-verified" not in json.dumps(page.metadata)
    assert client.usage["fetch"]["billable"] == 1


@pytest.mark.asyncio
async def test_target_404_does_not_trigger_fetch():
    from web_task_agent.deepseek_search import DeepSeekWebClient, FetchFallbackLoader

    async def removed(url):
        raise PageHttpError("gone", status_code=404)

    def forbidden(*args):
        pytest.fail("Do not resurrect removed jobs")

    client = DeepSeekWebClient(api_key="secret", transport=forbidden)
    with pytest.raises(PageHttpError):
        await FetchFallbackLoader(removed, client)("https://example.org/jobs/1")


@pytest.mark.asyncio
async def test_fetch_failure_and_budget_are_safe_and_distinct_from_target_404():
    from web_task_agent.deepseek_search import DeepSeekApiError, DeepSeekWebClient, FetchFallbackLoader

    async def empty(url):
        raise PageEmptyError("empty")

    def failed(*args):
        raise DeepSeekApiError(404)

    client = DeepSeekWebClient(api_key="secret", transport=failed)
    loader = FetchFallbackLoader(empty, client, max_calls=1)
    with pytest.raises(DeepSeekApiError) as error:
        await loader("https://example.org/jobs/1")
    assert not isinstance(error.value, PageHttpError)
    with pytest.raises(PageEmptyError):
        await loader("https://example.org/jobs/2")
    assert client.usage["fetch"]["attempts"] == 1
    assert client.usage["fetch"]["billable"] == 0


@pytest.mark.asyncio
async def test_good_primary_page_never_calls_fetch():
    from web_task_agent.deepseek_search import DeepSeekWebClient, FetchFallbackLoader

    async def primary(url):
        return BrowserPage(url=url, content="useful content " * 40)

    client = DeepSeekWebClient(api_key="secret", transport=lambda *args: pytest.fail("Unexpected fetch"))
    assert (await FetchFallbackLoader(primary, client)("https://example.org/jobs/1")).source == "browser"
