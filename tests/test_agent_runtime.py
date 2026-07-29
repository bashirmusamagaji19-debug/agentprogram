from __future__ import annotations

from types import SimpleNamespace

import pytest

from web_task_agent.agent_models import (
    AgentAction,
    AgentBudget,
    AgentDecision,
    DecisionAgentState,
    DecisionSource,
    ToolObservation,
)
from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.agent_runtime import HybridAgentRuntime
from web_task_agent.agent_tools import (
    AgentToolRegistry,
    ExtractTextTool,
    FinishTool,
    OpenPageTool,
    SaveResultsTool,
    ScoreMatchTool,
    SearchJobsTool,
    VerifyJobTool,
)
from web_task_agent.extractor import PageExtractor
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import BrowserPage, UserProfile
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow


class RecoveringBrowser:
    def __init__(self):
        self.opened = []

    async def search(self, query: str, target_count: int):
        return []

    async def open_url(self, url: str):
        self.opened.append(url)
        if url.endswith("/broken"):
            raise TimeoutError("timed out")
        return BrowserPage(
            url=url,
            title="AI Engineering Intern",
            content=(
                "Title: AI Engineering Intern\n"
                "Company: Example AI\n"
                "Location: Remote\n"
                "Requirements: Python, LangGraph, AI\n"
                "Responsibilities: Build AI agents"
            ),
        )


class InvalidPlanner:
    async def decide(self, state):
        return {"action": "delete_files", "reason": "invalid tool"}


class ExternalUrlPlanner:
    def __init__(self):
        self.calls = 0

    async def decide(self, state):
        self.calls += 1
        return AgentDecision(
            action=AgentAction.OPEN_PAGE,
            reason="Open a URL outside the discovered candidate set.",
            target="https://attacker.example/jobs/1",
        )


class InMemoryRepository:
    def save_jobs_once(self, jobs, *, idempotency_key):
        return SimpleNamespace(saved_jobs=len(jobs), reused=False)


def _registry(browser) -> AgentToolRegistry:
    return AgentToolRegistry(
        [
            SearchJobsTool(browser),
            OpenPageTool(browser),
            ExtractTextTool(PageExtractor()),
            VerifyJobTool(JobVerifier()),
            ScoreMatchTool(JobMatcher()),
            SaveResultsTool(InMemoryRepository()),
            FinishTool(),
        ]
    )


@pytest.mark.asyncio
async def test_runtime_recovers_open_failure_with_next_url():
    browser = RecoveringBrowser()
    broken = "https://example.com/jobs/broken"
    working = "https://example.com/jobs/working"
    state = DecisionAgentState(
        user=UserProfile(keyword="AI intern", target_count=1),
        budget=AgentBudget(max_steps=10),
        candidate_urls=[broken, working],
    )
    runtime = HybridAgentRuntime(
        registry=_registry(browser),
        policy=DeterministicAgentPolicy(),
    )

    result = await runtime.run(state)

    assert [decision.action for decision in result.decision_history] == [
        AgentAction.OPEN_PAGE,
        AgentAction.OPEN_PAGE,
        AgentAction.OPEN_PAGE,
        AgentAction.EXTRACT_TEXT,
        AgentAction.VERIFY_JOB,
        AgentAction.SCORE_MATCH,
        AgentAction.SAVE_RESULTS,
        AgentAction.FINISH,
    ]
    assert browser.opened == [broken, broken, working]
    assert result.terminal_status == "completed"
    assert result.terminal_reason == "target_reached"
    assert result.metrics.recovery_attempts == 2
    assert result.metrics.successful_recoveries == 1


@pytest.mark.asyncio
async def test_runtime_falls_back_after_invalid_planner_action():
    browser = RecoveringBrowser()
    state = DecisionAgentState(
        user=UserProfile(keyword="AI intern"),
        budget=AgentBudget(max_steps=1),
    )
    runtime = HybridAgentRuntime(
        registry=_registry(browser),
        policy=DeterministicAgentPolicy(),
        planner=InvalidPlanner(),
    )

    result = await runtime.run(state)

    assert result.metrics.invalid_actions >= 1
    assert result.metrics.fallback_decisions >= 1
    assert result.decision_history[0].source is DecisionSource.FALLBACK
    assert result.terminal_reason == "budget_exhausted"


@pytest.mark.asyncio
async def test_runtime_rejects_planner_url_outside_candidate_allowlist():
    browser = RecoveringBrowser()
    allowed = "https://example.com/jobs/working"
    state = DecisionAgentState(
        user=UserProfile(keyword="AI intern", target_count=1),
        budget=AgentBudget(max_steps=8),
        candidate_urls=[allowed],
    )
    planner = ExternalUrlPlanner()
    runtime = HybridAgentRuntime(
        registry=_registry(browser),
        policy=DeterministicAgentPolicy(),
        planner=planner,
    )

    result = await runtime.run(state)

    assert browser.opened == [allowed]
    assert result.decision_history[0].source is DecisionSource.FALLBACK
    assert result.metrics.invalid_actions >= 1
    assert result.metrics.fallback_decisions >= 1


@pytest.mark.asyncio
async def test_runtime_policy_owns_recovery_after_tool_failure():
    broken = "https://example.com/jobs/broken"
    working = "https://example.com/jobs/working"
    planner = ExternalUrlPlanner()
    state = DecisionAgentState(
        user=UserProfile(keyword="AI intern", target_count=1),
        budget=AgentBudget(max_steps=8),
        candidate_urls=[broken, working],
        current_url=broken,
        visited_urls={broken},
        retry_counts={broken: 2},
        last_observation=ToolObservation(
            tool_name=AgentAction.OPEN_PAGE,
            success=False,
            error_category="timeout",
            error_message="timed out",
            recoverable=True,
        ),
    )
    runtime = HybridAgentRuntime(
        registry=_registry(RecoveringBrowser()),
        policy=DeterministicAgentPolicy(),
        planner=planner,
    )

    updated = await runtime._decide_node(state)

    assert planner.calls == 0
    assert updated.last_decision.action is AgentAction.OPEN_PAGE
    assert updated.last_decision.target == working
    assert updated.last_decision.source is DecisionSource.POLICY
    assert updated.metrics.recovery_attempts == 1


@pytest.mark.asyncio
async def test_runtime_terminates_at_step_budget():
    browser = RecoveringBrowser()
    state = DecisionAgentState(
        user=UserProfile(keyword="AI intern"),
        budget=AgentBudget(max_steps=1),
        candidate_urls=["https://example.com/jobs/broken"],
    )
    runtime = HybridAgentRuntime(
        registry=_registry(browser),
        policy=DeterministicAgentPolicy(),
    )

    result = await runtime.run(state)

    assert result.budget.exhausted is True
    assert result.terminal_status in {"partial", "failed"}
    assert result.terminal_reason == "budget_exhausted"
    assert len(result.decision_history) == 2


def test_langgraph_contains_decision_observation_loop_nodes():
    runtime = HybridAgentRuntime(
        registry=AgentToolRegistry([FinishTool()]),
        policy=DeterministicAgentPolicy(),
    )

    graph_json = runtime.build_graph().get_graph().to_json()
    node_ids = {node["id"] for node in graph_json["nodes"]}

    assert {
        "initialize",
        "decide",
        "execute_tool",
        "observe",
        "guard",
        "finish",
    }.issubset(node_ids)


class CapturingRuntime:
    def __init__(self):
        self.state = None

    async def run(self, state):
        self.state = state
        return state


@pytest.mark.asyncio
async def test_workflow_exposes_hybrid_agent_entry_without_changing_baseline():
    workflow = WebTaskWorkflow(
        browser=object(),
        extractor=object(),
        matcher=object(),
        verifier=object(),
        repository=object(),
        reporter=object(),
        visual_extractor=object(),
    )
    runtime = CapturingRuntime()
    user = UserProfile(
        keyword="AI intern",
        seed_urls=["https://example.com/jobs/1"],
    )

    result = await workflow.run_with_hybrid_agent(
        user,
        runtime=runtime,
        max_steps=7,
    )

    assert result is runtime.state
    assert result.candidate_urls == user.seed_urls
    assert result.budget.max_steps == 7
    assert result.visual_available is True
