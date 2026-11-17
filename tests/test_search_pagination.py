from urllib.parse import parse_qs, urlsplit

import pytest

from web_task_agent.agent_models import AgentAction, AgentBudget, DecisionAgentState, ToolObservation
from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.browser import BrowserUseClient, PageTimeoutError
from web_task_agent.models import BrowserPage, JobPosting, UserProfile


def search_page(url, links):
    return BrowserPage(url=url, title="Search", content="AI intern results", metadata={"raw_links": links})


@pytest.mark.asyncio
async def test_search_follows_pages_and_counts_unique_candidates():
    opened = []

    async def loader(url):
        opened.append(url)
        offset = int(parse_qs(urlsplit(url).query).get("start", [0])[0])
        links = {0: ["https://example.com/jobs/1", "https://example.com/jobs/2"],
                 10: ["https://example.com/jobs/2?utm_source=test", "https://example.com/jobs/3"]}[offset]
        return search_page(url, links)

    pages = await BrowserUseClient(page_loader=loader).search("AI intern", target_count=3)
    assert [url for page in pages for url in page.metadata["candidate_urls"]] == [
        "https://example.com/jobs/1", "https://example.com/jobs/2", "https://example.com/jobs/3"]
    assert len(opened) == 2
    assert pages[-1].metadata["search_diagnostics"]["stop_reason"] == "target_reached"


@pytest.mark.asyncio
async def test_next_link_can_skip_page_without_job_links():
    opened = []

    async def loader(url):
        opened.append(url)
        return search_page(url, ["/search?q=AI&start=20"] if len(opened) == 1
                           else ["https://example.com/jobs/3"])

    pages = await BrowserUseClient(page_loader=loader).search("AI", target_count=1)
    assert opened[-1] == "https://www.google.com/search?q=AI&start=20"
    assert pages[-1].metadata["candidate_urls"] == ["https://example.com/jobs/3"]


@pytest.mark.asyncio
async def test_search_continues_after_previous_batch_without_returning_duplicates():
    opened = []

    async def loader(url):
        opened.append(url)
        return search_page(url, [f"https://example.com/jobs/{len(opened)}"])

    browser = BrowserUseClient(page_loader=loader)
    await browser.search("AI", target_count=1)
    second = await browser.search("AI", target_count=1)
    assert second[0].metadata["candidate_urls"] == ["https://example.com/jobs/2"]
    assert "start=10" in opened[1]


@pytest.mark.asyncio
async def test_repeated_pages_stop_without_claiming_target_reached():
    opened = []

    async def loader(url):
        opened.append(url)
        return search_page(url, ["https://example.com/jobs/1"])

    pages = await BrowserUseClient(page_loader=loader).search("AI", target_count=5)
    assert 1 < len(opened) <= 3
    assert pages[-1].metadata["search_diagnostics"]["stop_reason"] == "no_progress"


@pytest.mark.asyncio
async def test_later_page_failure_preserves_candidates():
    async def loader(url):
        if "start=" in url:
            raise PageTimeoutError("timeout")
        return search_page(url, ["https://example.com/jobs/1"])

    pages = await BrowserUseClient(page_loader=loader).search("AI", target_count=2)
    assert pages[0].metadata["candidate_urls"] == ["https://example.com/jobs/1"]
    assert pages[-1].metadata["search_diagnostics"]["stop_reason"] == "page_error"


def test_policy_searches_again_when_verified_target_not_met_and_candidates_exhausted():
    state = DecisionAgentState(user=UserProfile(keyword="AI", target_count=2),
                               candidate_urls=["https://example.com/jobs/1"],
                               processed_urls={"https://example.com/jobs/1"},
                               search_attempted=True, search_attempts=1, budget=AgentBudget(max_steps=20))
    assert DeterministicAgentPolicy().decide(state).action is AgentAction.SEARCH_JOBS


@pytest.mark.asyncio
async def test_page_limit_is_shared_across_search_batches_and_queries_are_independent():
    opened = []

    async def loader(url):
        opened.append(url)
        return search_page(url, [f"https://example.com/jobs/{len(opened)}"])

    browser = BrowserUseClient(page_loader=loader, max_search_pages=2)
    first = await browser.search("AI", target_count=5)
    assert len(opened) == 2
    assert first[-1].metadata["search_diagnostics"]["stop_reason"] == "page_limit"
    assert await browser.search("AI", target_count=5) == []
    assert (await browser.search("ML", target_count=1))[0].metadata["candidate_urls"]
    assert len(opened) == 3


@pytest.mark.asyncio
async def test_buffered_candidates_are_delivered_without_losing_extra_results():
    opened = []

    async def loader(url):
        opened.append(url)
        return search_page(url, [f"https://example.com/jobs/{i}" for i in range(5)])

    browser = BrowserUseClient(page_loader=loader)
    first = await browser.search("AI", target_count=2)
    second = await browser.search("AI", target_count=3)
    assert first[0].metadata["candidate_urls"] == [f"https://example.com/jobs/{i}" for i in range(2)]
    assert second[0].metadata["candidate_urls"] == [f"https://example.com/jobs/{i}" for i in range(2, 5)]
    assert len(opened) == 1


@pytest.mark.asyncio
async def test_deadline_bounds_injected_loader_and_keeps_earlier_results():
    import asyncio
    from time import monotonic

    async def loader(url):
        if "start=" in url:
            await asyncio.sleep(5)
        return search_page(url, ["https://example.com/jobs/1"])

    browser = BrowserUseClient(page_loader=loader, deadline=monotonic() + 0.05)
    pages = await asyncio.wait_for(browser.search("AI", target_count=2), timeout=1)
    assert pages[0].metadata["candidate_urls"] == ["https://example.com/jobs/1"]
    assert pages[-1].metadata["search_diagnostics"]["stop_reason"] == "deadline_exceeded"


def test_policy_preserves_verified_results_when_continued_search_has_no_candidates():
    state = DecisionAgentState(user=UserProfile(keyword="AI", target_count=2),
                               verified_jobs=[JobPosting(title="AI Intern", company="Acme", location="Remote",
                                                         url="https://example.com/jobs/1", source="fixture")],
                               search_attempted=True, search_attempts=2, budget=AgentBudget(max_steps=20),
                               last_observation=ToolObservation(tool_name=AgentAction.SEARCH_JOBS, success=False,
                                                                error_category="no_candidates"))
    assert DeterministicAgentPolicy().decide(state).action is AgentAction.SCORE_MATCH


def test_policy_does_not_search_after_search_adapter_reports_exhaustion():
    search = ToolObservation(tool_name=AgentAction.SEARCH_JOBS, success=True,
                             payload={"search_diagnostics": {"has_more": False}})
    state = DecisionAgentState(user=UserProfile(keyword="AI", target_count=2),
                               search_attempted=True, search_attempts=1, budget=AgentBudget(max_steps=20),
                               observation_history=[search])
    assert DeterministicAgentPolicy().decide(state).action is AgentAction.FINISH


@pytest.mark.asyncio
async def test_tool_classifies_later_page_error_with_zero_candidates_as_search_error():
    from web_task_agent.agent_tools import SearchJobsTool

    async def loader(url):
        if "start=" in url:
            raise PageTimeoutError("timeout")
        return search_page(url, ["/search?q=AI&start=10"])

    state = DecisionAgentState(user=UserProfile(keyword="AI"))
    observation = await SearchJobsTool(BrowserUseClient(page_loader=loader)).execute(state, {"query": "AI"})
    assert observation.error_category == "search_error"
    assert observation.recoverable


@pytest.mark.asyncio
async def test_runtime_replenishes_after_rejection_and_delivers_target_count():
    from tests.test_agent_runtime import _registry
    from web_task_agent.agent_runtime import HybridAgentRuntime

    searches = []

    async def loader(url):
        if "google.com/search" in url:
            searches.append(url)
            offset = int(parse_qs(urlsplit(url).query).get("start", [0])[0])
            job_id = {0: "bad", 10: "1", 20: "2"}[offset]
            return search_page(url, [f"https://example.com/jobs/{job_id}"])
        if url.endswith("/bad"):
            return BrowserPage(url=url, title="Unrelated", content="No job information")
        return BrowserPage(url=url, title=f"AI Intern {url[-1]}", content=(
            f"Title: AI Intern {url[-1]}\nCompany: Acme\nLocation: Remote\n"
            "Requirements: Python, LangGraph\nResponsibilities: Build AI agents"
        ))

    browser = BrowserUseClient(page_loader=loader)
    state = DecisionAgentState(user=UserProfile(keyword="AI", target_count=2), budget=AgentBudget(max_steps=20))
    result = await HybridAgentRuntime(registry=_registry(browser), policy=DeterministicAgentPolicy()).run(state)
    assert result.terminal_reason == "target_reached"
    assert result.saved
    assert len(result.verified_jobs) == 2
    assert result.search_attempts == 2
    assert len(searches) == 3
