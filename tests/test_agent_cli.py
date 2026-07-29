from __future__ import annotations

import pytest

from web_task_agent import cli as cli_module
from web_task_agent.agent_cli import (
    hybrid_state_payload,
    render_hybrid_html,
    render_hybrid_markdown,
)
from web_task_agent.agent_models import (
    AgentAction,
    AgentBudget,
    AgentDecision,
    DecisionAgentState,
    DecisionSource,
    ToolObservation,
)
from web_task_agent.cli import build_parser
from web_task_agent.models import UserProfile


def _completed_state() -> DecisionAgentState:
    decision = AgentDecision(
        action=AgentAction.OPEN_PAGE,
        reason="Open <script> candidate.",
        target="https://example.com/jobs/1",
        confidence=0.8,
        source=DecisionSource.LLM,
    )
    observation = ToolObservation(
        tool_name=AgentAction.OPEN_PAGE,
        success=True,
        summary="Opened candidate page.",
        latency_ms=12.5,
    )
    state = DecisionAgentState(
        user=UserProfile(keyword="AI intern"),
        budget=AgentBudget(max_steps=5, consumed_steps=1),
        last_decision=decision,
        last_observation=observation,
        decision_history=[decision],
        observation_history=[observation],
        terminal_status="completed",
        terminal_reason="target_reached",
    )
    state.metrics.tool_calls = 1
    state.metrics.successful_tool_calls = 1
    state.metrics.recovery_attempts = 1
    state.metrics.successful_recoveries = 1
    return state


def test_parser_accepts_hybrid_agent_flags():
    args = build_parser().parse_args(
        [
            "--keyword",
            "AI intern",
            "--hybrid-agent",
            "--agent-max-steps",
            "9",
            "--agent-planner-provider",
            "deepseek",
            "--agent-planner-model",
            "deepseek-chat",
        ]
    )

    assert args.hybrid_agent is True
    assert args.agent_max_steps == 9
    assert args.agent_planner_provider == "deepseek"
    assert args.agent_planner_model == "deepseek-chat"


def test_hybrid_payload_contains_decisions_observations_metrics_and_budget():
    payload = hybrid_state_payload(_completed_state())

    assert payload["orchestration_mode"] == "hybrid-agent"
    assert payload["terminal_reason"] == "target_reached"
    assert payload["trace"][0]["action"] == "open_page"
    assert payload["trace"][0]["source"] == "llm"
    assert payload["trace"][0]["observation"]["latency_ms"] == 12.5
    assert payload["metrics"]["recovery_success_rate"] == 1.0
    assert payload["budget"]["remaining_steps"] == 4


def test_hybrid_markdown_renders_agent_decision_evidence():
    markdown = render_hybrid_markdown(_completed_state())

    assert "Hybrid Decision Agent" in markdown
    assert "open_page" in markdown
    assert "Open <script> candidate." in markdown
    assert "Recovery success rate" in markdown
    assert "target_reached" in markdown


def test_hybrid_html_escapes_decision_reason():
    html = render_hybrid_html(_completed_state())

    assert "Hybrid Decision Agent" in html
    assert "Open &lt;script&gt; candidate." in html
    assert "Open <script> candidate." not in html


@pytest.mark.asyncio
async def test_cli_runs_deterministic_hybrid_agent_demo(monkeypatch):
    captured = {}

    def fake_write_artifacts(state, **kwargs):
        captured["state"] = state
        return {}

    monkeypatch.setattr(cli_module, "write_hybrid_artifacts", fake_write_artifacts)
    args = build_parser().parse_args(
        [
            "--keyword",
            "AI intern",
            "--demo",
            "--hybrid-agent",
            "--target-count",
            "1",
            "--agent-max-steps",
            "8",
            "--db-path",
            ":memory:",
        ]
    )

    exit_code = await cli_module._run(args)

    assert exit_code == 0
    assert captured["state"].terminal_status == "completed"
    assert captured["state"].decision_history
