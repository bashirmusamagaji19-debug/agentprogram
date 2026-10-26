from __future__ import annotations

import json

import pytest

from web_task_agent.agent_cli import hybrid_state_payload
from web_task_agent.agent_components import AgentServices
from web_task_agent.agent_models import DecisionAgentState
from web_task_agent.agent_service import AgentRunOptions, start_agent_run
from web_task_agent.models import UserProfile
from web_task_agent.streamlit_runner import UiRunRequest, UiRunResult, run_ui_request

from .test_agent_multitarget import make_workflow

A5_FIELDS = (
    "unique_candidates", "open_successes", "open_failures", "tool_latency_ms",
    "runtime_latency_ms", "approval_wait_ms", "model_usage_complete", "unmetered_model_roles",
)


@pytest.mark.asyncio
async def test_ui_json_exports_service_metrics_and_usage_completeness(tmp_path, monkeypatch):
    import web_task_agent.streamlit_runner as runner

    real_start = runner.start_agent_run
    captured = []

    async def start(*args, **kwargs):
        result = await real_start(*args, **kwargs)
        # Model components may lack usage even when planning uses policy only.
        result.view.metadata.update(model_usage_complete=False, unmetered_model_roles=["matcher"])
        captured.append(result)
        return result

    monkeypatch.setattr(runner, "start_agent_run", start)
    result = await run_ui_request(
        UiRunRequest(keyword="AI intern", location="全国", target_count=1, engine="hybrid"),
        output_root=tmp_path, environ={},
    )
    payload = json.loads(result.artifacts["json"].read_text(encoding="utf-8"))
    metadata = captured[0].view.metadata
    assert {field: payload[field] for field in A5_FIELDS} == {field: metadata[field] for field in A5_FIELDS}
    assert payload["budget"] == {
        **captured[0].agent_state.budget.model_dump(mode="json"),
        "remaining_steps": captured[0].agent_state.budget.remaining_steps,
    }
    assert payload["agent_trace"] == metadata["agent_trace"]
    assert payload["agent_trace"][-1]["remaining_budget"] == (
        payload["budget"]["max_steps"] - payload["budget"]["consumed_steps"]
    )
    assert all("planner_usage" in row for row in payload["agent_trace"])


@pytest.mark.asyncio
async def test_cli_payload_exports_canonical_trace_and_metrics(tmp_path):
    workflow, pages = make_workflow(tmp_path, count=1)
    result = await start_agent_run(
        UserProfile(keyword="AI intern", location="Remote", target_count=1, seed_urls=[pages[0].url]),
        run_id="export-cli", owner_id="owner",
        services=AgentServices(workflow=workflow, run_dir=tmp_path),
        options=AgentRunOptions(engine="hybrid"),
    )
    state = result.agent_state
    state.unmetered_model_roles = ["reader"]
    payload = hybrid_state_payload(state)
    assert payload["agent_trace"] == result.view.metadata["agent_trace"]
    assert payload["trace"][0]["remaining_budget"] == payload["agent_trace"][0]["remaining_budget"]
    assert all("planner_usage" in row for row in payload["trace"])
    for field in A5_FIELDS[:6]:
        assert payload[field] == result.view.metadata[field]
    assert payload["model_usage_complete"] is False
    assert payload["unmetered_model_roles"] == ["reader"]
    assert payload["metrics"]["estimated_token_cost"] is None
    for field, value in result.view.metrics.model_dump(mode="json").items():
        if field not in {"started_at", "finished_at"}:
            assert payload["metrics"][field] == value
    assert payload["model_usage"] == state.model_usage
    assert payload["planner_routes"] == state.planner_routes


def test_old_ui_results_get_safe_export_defaults():
    result = UiRunResult.model_validate({"run_id": "old", "metrics": {"run_id": "old"}})
    assert result.unique_candidates == result.open_successes == result.open_failures == 0
    assert result.tool_latency_ms == result.runtime_latency_ms == result.approval_wait_ms == 0
    assert result.budget == {}
    assert result.agent_trace == []
    assert result.model_usage_complete is False
    assert result.unmetered_model_roles == []


def test_cli_does_not_treat_partial_usage_as_complete():
    state = DecisionAgentState(user=UserProfile(keyword="AI intern"))
    state.model_usage = [{"total_tokens": 12, "usage_complete": False}]
    assert hybrid_state_payload(state)["model_usage_complete"] is False
    state.model_usage[0]["usage_complete"] = True
    assert hybrid_state_payload(state)["model_usage_complete"] is True


def test_cli_keeps_per_decision_planner_usage_records():
    from web_task_agent.agent_models import AgentAction, AgentBudget, AgentDecision, ToolObservation

    usage = {"total_tokens": 12, "usage_complete": True}
    state = DecisionAgentState(
        user=UserProfile(keyword="AI intern"), budget=AgentBudget(max_steps=8, consumed_steps=1),
        decision_history=[AgentDecision(action=AgentAction.OPEN_PAGE, reason="Pick a candidate")],
        observation_history=[ToolObservation(tool_name=AgentAction.OPEN_PAGE, success=True)],
        decision_telemetry=[{"remaining_budget": 8, "planner_usage": [usage]}],
        model_usage=[usage],
    )
    payload = hybrid_state_payload(state)
    assert payload["agent_trace"][0]["planner_usage"] == [usage]
    assert payload["trace"][0]["planner_usage"] == [usage]
    assert payload["agent_trace"][0]["remaining_budget"] == 7
