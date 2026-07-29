from __future__ import annotations

from types import SimpleNamespace

import pytest

from web_task_agent.agent_approval import (
    ApprovalDecision,
    ApprovalOutcome,
    HitlRunStatus,
    HitlRuntimeError,
)
from web_task_agent.agent_checkpoint import open_sqlite_checkpointer
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
from web_task_agent.models import BrowserPage, JobPosting, UserProfile
from web_task_agent.storage import JobRepository
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


def _verified_job() -> JobPosting:
    return JobPosting(
        title="AI Engineering Intern",
        company="Example AI",
        location="Remote",
        source="fixture",
        url="https://example.com/jobs/1",
        requirements="Python, LangGraph",
        responsibilities="Build AI agents",
        skills=["Python", "LangGraph"],
        confidence=0.9,
    )


def _target_ready_state() -> DecisionAgentState:
    return DecisionAgentState(
        user=UserProfile(
            keyword="AI intern",
            target_count=1,
            skills=["Python", "LangGraph"],
        ),
        budget=AgentBudget(max_steps=6),
        verified_jobs=[_verified_job()],
    )


def _hitl_runtime(repository, checkpointer) -> HybridAgentRuntime:
    return HybridAgentRuntime(
        registry=AgentToolRegistry(
            [
                ScoreMatchTool(JobMatcher()),
                SaveResultsTool(repository),
                FinishTool(),
            ]
        ),
        policy=DeterministicAgentPolicy(),
        checkpointer=checkpointer,
    )


def _initialized_repository(path) -> JobRepository:
    repository = JobRepository(path)
    repository.initialize()
    return repository


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


@pytest.mark.asyncio
async def test_hitl_pauses_before_save_without_side_effect(tmp_path):
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    repository = _initialized_repository(tmp_path / "jobs.sqlite")

    async with open_sqlite_checkpointer(checkpoint_path) as saver:
        result = await _hitl_runtime(repository, saver).start_hitl(
            _target_ready_state(),
            thread_id="thread-pause",
        )

    assert result.status is HitlRunStatus.AWAITING_APPROVAL
    assert result.approval is not None
    assert result.approval.action == "save_results"
    assert result.state.budget.consumed_steps == 1
    assert [item.action for item in result.state.decision_history] == [
        AgentAction.SCORE_MATCH,
        AgentAction.SAVE_RESULTS,
    ]
    assert repository.list_jobs() == []


@pytest.mark.asyncio
async def test_hitl_approve_resumes_from_another_runtime_once(tmp_path):
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    repository = _initialized_repository(tmp_path / "jobs.sqlite")

    async with open_sqlite_checkpointer(checkpoint_path) as saver:
        paused = await _hitl_runtime(repository, saver).start_hitl(
            _target_ready_state(),
            thread_id="thread-approve",
        )

    async with open_sqlite_checkpointer(checkpoint_path) as saver:
        completed = await _hitl_runtime(repository, saver).resume_hitl(
            thread_id="thread-approve",
            decision=ApprovalDecision(
                approval_id=paused.approval.approval_id,
                outcome=ApprovalOutcome.APPROVE,
            ),
        )

    assert completed.status is HitlRunStatus.COMPLETED
    assert completed.state.terminal_reason == "target_reached"
    assert len(repository.list_jobs()) == 1
    save_observation = next(
        item
        for item in completed.state.observation_history
        if item.tool_name is AgentAction.SAVE_RESULTS
    )
    assert save_observation.payload == {"saved_jobs": 1, "reused": False}
    assert [event.event for event in completed.state.approval_audit] == [
        "requested",
        "resolved",
    ]


@pytest.mark.asyncio
async def test_hitl_reject_never_executes_save(tmp_path):
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    repository = _initialized_repository(tmp_path / "jobs.sqlite")

    async with open_sqlite_checkpointer(checkpoint_path) as saver:
        runtime = _hitl_runtime(repository, saver)
        paused = await runtime.start_hitl(
            _target_ready_state(),
            thread_id="thread-reject",
        )
        rejected = await runtime.resume_hitl(
            thread_id="thread-reject",
            decision=ApprovalDecision(
                approval_id=paused.approval.approval_id,
                outcome=ApprovalOutcome.REJECT,
                note="Do not persist",
            ),
        )

    assert rejected.status is HitlRunStatus.REJECTED
    assert rejected.state.terminal_status == "rejected"
    assert rejected.state.terminal_reason == "human_denied"
    assert repository.list_jobs() == []
    assert all(
        item.tool_name is not AgentAction.SAVE_RESULTS
        for item in rejected.state.observation_history
    )


@pytest.mark.asyncio
async def test_hitl_rejects_missing_mismatched_and_duplicate_resume(tmp_path):
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    repository = _initialized_repository(tmp_path / "jobs.sqlite")

    async with open_sqlite_checkpointer(checkpoint_path) as saver:
        runtime = _hitl_runtime(repository, saver)
        with pytest.raises(HitlRuntimeError, match="was not found"):
            await runtime.resume_hitl(
                thread_id="missing",
                decision=ApprovalDecision(
                    approval_id="approval-missing",
                    outcome=ApprovalOutcome.APPROVE,
                ),
            )

        paused = await runtime.start_hitl(
            _target_ready_state(),
            thread_id="thread-errors",
        )
        with pytest.raises(HitlRuntimeError, match="does not match"):
            await runtime.resume_hitl(
                thread_id="thread-errors",
                decision=ApprovalDecision(
                    approval_id="approval-wrong",
                    outcome=ApprovalOutcome.APPROVE,
                ),
            )

        await runtime.resume_hitl(
            thread_id="thread-errors",
            decision=ApprovalDecision(
                approval_id=paused.approval.approval_id,
                outcome=ApprovalOutcome.APPROVE,
            ),
        )
        with pytest.raises(HitlRuntimeError, match="no pending approval"):
            await runtime.resume_hitl(
                thread_id="thread-errors",
                decision=ApprovalDecision(
                    approval_id=paused.approval.approval_id,
                    outcome=ApprovalOutcome.APPROVE,
                ),
            )


class CapturingRuntime:
    def __init__(self):
        self.state = None

    async def run(self, state):
        self.state = state
        return state

    async def start_hitl(self, state, *, thread_id):
        self.state = state
        self.thread_id = thread_id
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


@pytest.mark.asyncio
async def test_workflow_exposes_hitl_hybrid_agent_entry():
    workflow = WebTaskWorkflow(
        browser=object(),
        extractor=object(),
        matcher=object(),
        verifier=object(),
        repository=object(),
        reporter=object(),
    )
    runtime = CapturingRuntime()
    user = UserProfile(
        keyword="AI intern",
        seed_urls=["https://example.com/jobs/1"],
    )

    result = await workflow.start_with_hybrid_agent_hitl(
        user,
        runtime=runtime,
        thread_id="thread-1",
        max_steps=9,
    )

    assert result is runtime.state
    assert runtime.thread_id == "thread-1"
    assert result.candidate_urls == user.seed_urls
    assert result.budget.max_steps == 9
