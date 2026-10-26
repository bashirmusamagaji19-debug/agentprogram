from __future__ import annotations

import asyncio
import threading
from time import monotonic

import pytest

from web_task_agent.agent_approval import ApprovalDecision, HitlRunStatus
from web_task_agent.agent_checkpoint import open_sqlite_checkpointer
from web_task_agent.agent_models import AgentAction, AgentBudget, AgentDecision, DecisionAgentState, DecisionSource
from web_task_agent.agent_planner import OpenAiCompatibleAgentPlanner
from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.agent_runtime import HybridAgentRuntime
from web_task_agent.agent_tools import AgentToolRegistry, FinishTool, SaveResultsTool, ScoreMatchTool
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import UserProfile
from web_task_agent.planner_routing import ModelBudgetExceeded, ModelCallBudget

from .test_agent_runtime import RecoveringBrowser, _initialized_repository, _registry, _target_ready_state


def candidate_state():
    return DecisionAgentState(user=UserProfile(keyword="AI intern", target_count=1),
                              candidate_urls=["https://example.com/jobs/allowed"],
                              budget=AgentBudget(max_steps=12))


@pytest.mark.asyncio
async def test_planner_mutations_cannot_widen_browser_authorization_or_mutate_nested_state():
    attacker = "https://attacker.example/jobs/1"

    class Planner:
        async def decide(self, state):
            state.candidate_urls.append(attacker)
            state.allowed_targets["open_page"].append(attacker)
            state.user.keyword = "changed"
            state.candidate_summaries[state.candidate_urls[0]]["title"] = "changed"
            state.budget = state.budget.consume()
            return AgentDecision(action=AgentAction.OPEN_PAGE, reason="open", target=attacker)

    browser = RecoveringBrowser()
    state = candidate_state()
    state.candidate_summaries[state.candidate_urls[0]] = {"title": "AI Intern"}
    result = await HybridAgentRuntime(registry=_registry(browser), policy=DeterministicAgentPolicy(),
                                      planner=Planner()).run(state)
    assert attacker not in browser.opened
    assert result.user.keyword == "AI intern"
    assert result.candidate_summaries[state.candidate_urls[0]]["title"] == "AI Intern"
    assert result.decision_history[0].source is DecisionSource.FALLBACK
    assert result.budget.consumed_steps == 5
    assert result.terminal_status == "completed"


class SlowBrowser(RecoveringBrowser):
    def __init__(self):
        super().__init__()
        self.cancelled = False

    async def open_url(self, url):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return await super().open_url(url)


@pytest.mark.parametrize("hitl", [False, True])
@pytest.mark.asyncio
async def test_direct_runtime_cancels_tool_at_deadline_and_records_consumed_attempt(tmp_path, hitl):
    browser = SlowBrowser()
    state = candidate_state()
    async with open_sqlite_checkpointer(tmp_path / "checkpoint.sqlite") as saver:
        runtime = HybridAgentRuntime(registry=_registry(browser), policy=DeterministicAgentPolicy(),
                                      deadline_seconds=0.2, checkpointer=saver)
        started = monotonic()
        if hitl:
            result = (await asyncio.wait_for(runtime.start_hitl(state, thread_id="timeout"), 2)).state
        else:
            result = await asyncio.wait_for(runtime.run(state), 2)
        assert monotonic() - started < 2
        assert browser.cancelled
        assert not browser.opened
        assert result.terminal_reason == "deadline_exceeded"
        assert result.terminal_status == "failed"
        assert not result.saved
        assert result.budget.consumed_steps == 1
        assert len(result.decision_history) == len(result.observation_history) == 2
        assert result.observation_history[0].tool_name is AgentAction.OPEN_PAGE
        assert result.observation_history[0].error_category == "deadline_exceeded"
        assert not result.observation_history[0].success
        assert result.metrics.successful_tool_calls == 1
        assert result.decision_telemetry[0]["observed_remaining_budget"] == result.budget.remaining_steps
        assert result.decision_telemetry[-1]["observed_remaining_budget"] == result.budget.remaining_steps
        if hitl:
            loaded = await runtime.load_hitl_checkpoint("timeout")
            assert loaded.state.terminal_reason == "deadline_exceeded"
            assert not loaded.has_interrupt
            assert not loaded.next_nodes


@pytest.mark.parametrize("resume_method", ["resume_hitl", "recover_hitl"])
@pytest.mark.asyncio
async def test_direct_hitl_resume_is_bounded_and_timeout_persists_terminal_state(tmp_path, resume_method):
    class SlowSave:
        name = AgentAction.SAVE_RESULTS

        async def execute(self, state, arguments):
            await asyncio.sleep(10)
            raise AssertionError("must be cancelled")

    async with open_sqlite_checkpointer(tmp_path / "checkpoint.sqlite") as saver:
        runtime = HybridAgentRuntime(
            registry=AgentToolRegistry([ScoreMatchTool(JobMatcher()), SlowSave(), FinishTool()]),
            policy=DeterministicAgentPolicy(), checkpointer=saver, deadline_seconds=0.3,
        )
        paused = await runtime.start_hitl(_target_ready_state(), thread_id="resume")
        decision = ApprovalDecision(approval_id=paused.approval.approval_id, outcome="approve")
        result = await asyncio.wait_for(getattr(runtime, resume_method)(thread_id="resume", decision=decision), 2)
        assert result.status is HitlRunStatus.FAILED
        assert result.state.terminal_reason == "deadline_exceeded"
        assert not result.state.saved
        assert result.state.observation_history[-2].tool_name is AgentAction.SAVE_RESULTS
        assert result.state.observation_history[-2].error_category == "deadline_exceeded"
        loaded = await runtime.load_hitl_checkpoint("resume")
        assert loaded.state.terminal_reason == "deadline_exceeded"
        assert not loaded.next_nodes
        assert not loaded.has_interrupt


@pytest.mark.parametrize("resume_method", ["resume_hitl", "recover_hitl"])
@pytest.mark.asyncio
async def test_human_wait_does_not_spend_hitl_active_deadline(tmp_path, resume_method):
    repository = _initialized_repository(tmp_path / "jobs.sqlite")
    async with open_sqlite_checkpointer(tmp_path / "checkpoint.sqlite") as saver:
        runtime = HybridAgentRuntime(
            registry=AgentToolRegistry([ScoreMatchTool(JobMatcher()), SaveResultsTool(repository), FinishTool()]),
            policy=DeterministicAgentPolicy(), checkpointer=saver, deadline_seconds=0.3,
        )
        paused = await runtime.start_hitl(_target_ready_state(), thread_id="wait")
        await asyncio.sleep(0.4)
        result = await getattr(runtime, resume_method)(
            thread_id="wait", decision=ApprovalDecision(approval_id=paused.approval.approval_id, outcome="approve"),
        )
        assert result.status is HitlRunStatus.COMPLETED
        assert len(repository.list_jobs()) == 1


@pytest.mark.asyncio
async def test_zero_admission_budget_has_no_actual_calls_or_fallbacks():
    requests = []

    def transport(*args):
        requests.append(args)
        raise AssertionError("zero budget must not reach transport")

    planner = OpenAiCompatibleAgentPlanner(provider="qwen", model="fixture", api_key="fixture",
                                          budget=ModelCallBudget(max_calls=0), transport=transport)
    result = await HybridAgentRuntime(registry=_registry(RecoveringBrowser()),
                                      policy=DeterministicAgentPolicy(), planner=planner).run(candidate_state())
    assert not requests
    assert result.metrics.planner_calls == result.metrics.fallback_decisions == 0
    assert result.metrics.budget_skips > 0
    assert result.terminal_status == "completed"


@pytest.mark.parametrize("reject", [False, True])
@pytest.mark.asyncio
async def test_custom_planner_counts_invocations_except_explicit_admission_rejection(reject):
    class Planner:
        calls = 0

        async def decide(self, state):
            self.calls += 1
            if reject:
                raise ModelBudgetExceeded("admission rejected")
            raise RuntimeError("failed actual invocation")

    planner = Planner()
    result = await HybridAgentRuntime(registry=_registry(RecoveringBrowser()),
                                      policy=DeterministicAgentPolicy(), planner=planner).run(candidate_state())
    assert planner.calls > 0
    assert result.metrics.planner_calls == (0 if reject else planner.calls)
    assert result.metrics.fallback_decisions == (0 if reject else planner.calls)


@pytest.mark.asyncio
async def test_admitted_timeout_counts_once_with_unknown_usage_and_deterministic_fallback():
    def transport(*args):
        raise TimeoutError("fixture timeout")

    budget = ModelCallBudget(max_calls=4)
    planner = OpenAiCompatibleAgentPlanner(provider="qwen", model="fixture", api_key="fixture",
                                          budget=budget, transport=transport)
    result = await HybridAgentRuntime(registry=_registry(RecoveringBrowser()),
                                      policy=DeterministicAgentPolicy(), planner=planner).run(candidate_state())
    assert budget.calls == result.metrics.planner_calls == result.metrics.fallback_decisions == 1
    assert budget.observed_tokens is None
    assert result.model_usage[0]["total_tokens"] is None
    assert result.decision_history[0].source is DecisionSource.FALLBACK
    assert result.terminal_status == "completed"


@pytest.mark.asyncio
async def test_normal_observations_expose_actual_remaining_budget():
    result = await HybridAgentRuntime(registry=_registry(RecoveringBrowser()),
                                      policy=DeterministicAgentPolicy()).run(candidate_state())
    expected = result.budget.max_steps
    for decision, row in zip(result.decision_history, result.decision_telemetry, strict=True):
        if decision.action is not AgentAction.FINISH:
            expected -= 1
        assert row["observed_remaining_budget"] == expected
    assert expected == result.budget.remaining_steps


@pytest.mark.asyncio
async def test_run_deadline_during_admitted_planner_keeps_unknown_usage_without_tool_attempt():
    released = threading.Event()

    def transport(*args):
        released.wait(2)
        return {"choices": [{"message": {"content": '{"action":"open_page","reason":"late"}'}}]}

    budget = ModelCallBudget(max_calls=2)
    planner = OpenAiCompatibleAgentPlanner(provider="qwen", model="fixture", api_key="fixture",
                                          budget=budget, transport=transport)
    browser = RecoveringBrowser()
    runtime = HybridAgentRuntime(registry=_registry(browser), policy=DeterministicAgentPolicy(),
                                  planner=planner, deadline_seconds=0.2, planner_timeout_seconds=5)
    try:
        result = await asyncio.wait_for(runtime.run(candidate_state()), 2)
        assert not browser.opened
        assert result.budget.consumed_steps == 0
        assert result.metrics.planner_calls == budget.calls == 1
        assert budget.observed_tokens is None
        assert result.model_usage[0]["total_tokens"] is None
        assert not result.model_usage[0]["usage_complete"]
        assert result.decision_telemetry[-1]["planner_usage"] == result.model_usage
    finally:
        released.set()


@pytest.mark.asyncio
async def test_deadline_projection_is_idempotent():
    browser = SlowBrowser()
    runtime = HybridAgentRuntime(registry=_registry(browser), policy=DeterministicAgentPolicy(),
                                  deadline_seconds=0.2)
    result = await runtime.run(candidate_state())
    snapshot = result.model_dump()
    assert runtime.deadline_state(result).model_dump() == snapshot


@pytest.mark.asyncio
async def test_timeout_between_tool_and_observe_preserves_completed_observation():
    class SlowObserveRuntime(HybridAgentRuntime):
        async def _observe_node(self, state):
            await asyncio.sleep(10)
            return await super()._observe_node(state)

    browser = RecoveringBrowser()
    runtime = SlowObserveRuntime(registry=_registry(browser), policy=DeterministicAgentPolicy(),
                                  deadline_seconds=0.2)
    result = await runtime.run(candidate_state())
    assert len(browser.opened) == 1
    assert result.observation_history[0].success
    assert result.observation_history[0].summary.startswith("Opened")
    assert result.budget.consumed_steps == 1


@pytest.mark.asyncio
async def test_timeout_before_tool_execution_does_not_consume_a_tool_attempt():
    class SlowDispatchRuntime(HybridAgentRuntime):
        async def _execute_tool_node(self, state):
            await asyncio.sleep(10)
            return await super()._execute_tool_node(state)

    browser = RecoveringBrowser()
    runtime = SlowDispatchRuntime(registry=_registry(browser), policy=DeterministicAgentPolicy(),
                                   deadline_seconds=0.2)
    result = await runtime.run(candidate_state())
    assert not browser.opened
    assert result.budget.consumed_steps == 0
    assert not result.observation_history[0].success
    assert result.decision_telemetry[0]["observed_remaining_budget"] == 12


@pytest.mark.parametrize("entry", ["start_hitl", "resume_hitl", "recover_hitl"])
@pytest.mark.asyncio
async def test_hitl_entry_bounds_stalled_checkpoint_read(tmp_path, entry, monkeypatch):
    async with open_sqlite_checkpointer(tmp_path / "checkpoint.sqlite") as saver:
        runtime = HybridAgentRuntime(registry=_registry(RecoveringBrowser()), policy=DeterministicAgentPolicy(),
                                      checkpointer=saver, deadline_seconds=0.2)

        async def stalled_read(*args, **kwargs):
            await asyncio.sleep(10)

        monkeypatch.setattr(runtime._graph(hitl=True), "aget_state", stalled_read)
        started = monotonic()
        with pytest.raises(TimeoutError):
            if entry == "start_hitl":
                await asyncio.wait_for(runtime.start_hitl(candidate_state(), thread_id="stalled"), 2)
            else:
                await asyncio.wait_for(getattr(runtime, entry)(
                    thread_id="stalled", decision=ApprovalDecision(approval_id="unread", outcome="approve"),
                ), 2)
        assert monotonic() - started < 1.5


@pytest.mark.asyncio
async def test_rejected_approval_records_observed_budget_without_consuming_save(tmp_path):
    repository = _initialized_repository(tmp_path / "jobs.sqlite")
    async with open_sqlite_checkpointer(tmp_path / "checkpoint.sqlite") as saver:
        runtime = HybridAgentRuntime(
            registry=AgentToolRegistry([ScoreMatchTool(JobMatcher()), SaveResultsTool(repository), FinishTool()]),
            policy=DeterministicAgentPolicy(), checkpointer=saver,
        )
        paused = await runtime.start_hitl(_target_ready_state(), thread_id="reject")
        result = await runtime.resume_hitl(thread_id="reject", decision=ApprovalDecision(
            approval_id=paused.approval.approval_id, outcome="reject",
        ))
        assert result.status is HitlRunStatus.REJECTED
        assert result.state.budget.consumed_steps == 1
        assert result.state.decision_telemetry[-1]["observed_remaining_budget"] == 5
        assert not repository.list_jobs()
