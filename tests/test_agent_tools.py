from __future__ import annotations

import pytest

from web_task_agent.agent_models import (
    AgentAction,
    AgentBudget,
    AgentDecision,
    DecisionAgentState,
    ToolObservation,
)
from web_task_agent.agent_tools import (
    AgentToolRegistry,
    ExtractTextTool,
    ExtractVisualTool,
    FinishTool,
    OpenPageTool,
    SaveResultsTool,
    ScoreMatchTool,
    SearchJobsTool,
    VerifyJobTool,
)
from web_task_agent.extractor import PageExtractor
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import BrowserPage, JobPosting, UserProfile
from web_task_agent.verifier import JobVerifier
from web_task_agent.visual_extractor import DemoVisualJobExtractor


class SuccessfulTool:
    name = AgentAction.SEARCH_JOBS

    async def execute(self, state, arguments):
        return ToolObservation(
            tool_name=self.name,
            success=True,
            summary="found candidates",
            payload={"candidate_urls": ["https://example.com/jobs/1"]},
        )


class BrokenBrowser:
    async def open_url(self, url: str):
        raise TimeoutError("navigation timed out")


class StaticBrowser:
    async def search(self, query: str, target_count: int):
        return [
            BrowserPage(
                url="https://example.com/jobs/1",
                title="AI Engineering Intern",
                content="AI intern role",
            )
        ]

    async def open_url(self, url: str):
        return BrowserPage(
            url=url,
            title="AI Engineering Intern",
            content=(
                "Title: AI Engineering Intern\n"
                "Company: Example AI\n"
                "Location: Remote\n"
                "Requirements: Python, LangGraph\n"
                "Responsibilities: Build AI agents"
            ),
        )


class RecordingRepository:
    def __init__(self):
        self.jobs = []

    def save_jobs(self, jobs):
        self.jobs = list(jobs)


def _state() -> DecisionAgentState:
    return DecisionAgentState(
        user=UserProfile(keyword="AI intern"),
        budget=AgentBudget(max_steps=5),
    )


@pytest.mark.asyncio
async def test_registry_executes_allowed_tool_and_records_latency():
    registry = AgentToolRegistry([SuccessfulTool()])

    observation = await registry.execute(
        AgentDecision(
            action=AgentAction.SEARCH_JOBS,
            reason="find candidate jobs",
        ),
        _state(),
    )

    assert observation.success is True
    assert observation.payload["candidate_urls"]
    assert observation.latency_ms >= 0


@pytest.mark.asyncio
async def test_registry_returns_failed_observation_for_unregistered_action():
    observation = await AgentToolRegistry([]).execute(
        AgentDecision(
            action=AgentAction.FINISH,
            reason="finish",
        ),
        _state(),
    )

    assert observation.success is False
    assert observation.error_category == "unregistered_tool"
    assert observation.recoverable is False


@pytest.mark.asyncio
async def test_open_page_tool_converts_exception_to_recoverable_observation():
    state = _state()
    tool = OpenPageTool(BrokenBrowser())

    observation = await tool.execute(
        state,
        {"url": "https://example.com/jobs/1"},
    )

    assert observation.success is False
    assert observation.error_category == "page_timeout"
    assert observation.recoverable is True
    assert state.retry_counts["https://example.com/jobs/1"] == 1


@pytest.mark.asyncio
async def test_open_page_and_extract_text_tools_update_state():
    state = _state()
    open_observation = await OpenPageTool(StaticBrowser()).execute(
        state,
        {"url": "https://example.com/jobs/1"},
    )
    extract_observation = await ExtractTextTool(PageExtractor()).execute(state, {})

    assert open_observation.success is True
    assert state.current_page is not None
    assert extract_observation.success is True
    assert extract_observation.payload["confidence"] == 1.0
    assert state.extracted_jobs[0].company == "Example AI"


@pytest.mark.asyncio
async def test_search_tool_enqueues_candidate_urls():
    state = _state()

    observation = await SearchJobsTool(StaticBrowser()).execute(
        state,
        {"query": "AI intern Remote", "target_count": 2},
    )

    assert observation.success is True
    assert state.candidate_urls == ["https://example.com/jobs/1"]


@pytest.mark.asyncio
async def test_search_tool_does_not_enqueue_search_page_when_discovery_is_empty():
    class EmptyDiscoveryBrowser:
        async def search(self, query: str, target_count: int):
            return [
                BrowserPage(
                    url="https://www.google.com/search?q=AI+intern",
                    title="Search results",
                    content="No job links",
                    metadata={"candidate_urls": []},
                )
            ]

    state = _state()
    observation = await SearchJobsTool(EmptyDiscoveryBrowser()).execute(state, {})

    assert observation.success is False
    assert observation.error_category == "no_candidates"
    assert state.candidate_urls == []


@pytest.mark.asyncio
async def test_visual_verify_match_save_and_finish_tools_share_state():
    url = "https://example.com/jobs/visual-ai-intern"
    state = _state().model_copy(
        update={
            "current_url": url,
            "current_page": BrowserPage(url=url, title="Visual AI Intern", content=""),
            "visual_available": True,
        }
    )
    visual = await ExtractVisualTool(DemoVisualJobExtractor()).execute(state, {})
    verified = await VerifyJobTool(JobVerifier()).execute(state, {})
    matched = await ScoreMatchTool(JobMatcher()).execute(state, {})
    repository = RecordingRepository()
    saved = await SaveResultsTool(repository).execute(state, {})
    finished = await FinishTool().execute(
        state,
        {"terminal_reason": "target_reached"},
    )

    assert visual.success is True
    assert verified.success is True
    assert state.verified_jobs[0].title == "Visual AI Intern"
    assert matched.success is True
    assert len(state.matches) == 1
    assert saved.success is True
    assert repository.jobs == state.verified_jobs
    assert finished.success is True
    assert state.terminal_status == "completed"
    assert state.terminal_reason == "target_reached"
