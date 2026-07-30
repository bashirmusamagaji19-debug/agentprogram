from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from web_task_agent import cli as cli_module
from web_task_agent.agent_cli import (
    build_hybrid_runtime,
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
from web_task_agent.cli import build_parser, validate_hitl_args
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


def test_parser_accepts_hitl_checkpoint_flags():
    args = build_parser().parse_args(
        [
            "--hybrid-agent",
            "--hitl",
            "--thread-id",
            "demo-1",
            "--checkpoint-db",
            ".agent/checkpoints.sqlite",
            "--resume-approval",
            "approve",
            "--approval-id",
            "approval-1",
            "--approval-note",
            "Reviewed",
        ]
    )

    assert args.hitl is True
    assert args.thread_id == "demo-1"
    assert args.checkpoint_db == ".agent/checkpoints.sqlite"
    assert args.resume_approval == "approve"
    assert args.approval_id == "approval-1"
    assert args.approval_note == "Reviewed"


def test_build_hybrid_runtime_injects_checkpointer():
    checkpointer = object()
    workflow = SimpleNamespace(
        browser=object(),
        extractor=object(),
        matcher=object(),
        verifier=object(),
        repository=object(),
        visual_extractor=None,
    )

    runtime = build_hybrid_runtime(workflow, checkpointer=checkpointer)

    assert runtime.checkpointer is checkpointer


def test_parser_accepts_planner_benchmark_flags():
    args = build_parser().parse_args(
        [
            "--agent-planner-benchmark",
            "--agent-planner-benchmark-providers",
            "deterministic,deepseek",
            "--agent-planner-benchmark-output-dir",
            "outputs/planner",
        ]
    )

    assert args.agent_planner_benchmark is True
    assert args.agent_planner_benchmark_providers == "deterministic,deepseek"
    assert args.agent_planner_benchmark_output_dir == "outputs/planner"


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


@pytest.mark.asyncio
async def test_cli_hitl_pause_prints_resume_identity(monkeypatch, capsys, tmp_path):
    captured = {}

    def fake_write_artifacts(state, **kwargs):
        captured["state"] = state
        return {}

    monkeypatch.setattr(cli_module, "write_hybrid_artifacts", fake_write_artifacts)
    args = build_parser().parse_args(
        [
            "--demo",
            "--hybrid-agent",
            "--hitl",
            "--thread-id",
            "demo-1",
            "--checkpoint-db",
            str(tmp_path / "checkpoints.sqlite"),
            "--db-path",
            str(tmp_path / "jobs.sqlite"),
            "--keyword",
            "AI intern",
            "--target-count",
            "1",
            "--agent-max-steps",
            "8",
        ]
    )

    exit_code = await cli_module._run(args)
    output = capsys.readouterr().out

    assert exit_code == 0
    assert captured["state"].pending_approval is not None
    assert "awaiting_approval" in output
    assert "Thread ID: demo-1" in output
    assert "--resume-approval approve" in output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--hitl", "--thread-id", "demo-1"], "requires --hybrid-agent"),
        (["--hybrid-agent", "--hitl"], "requires --thread-id"),
        (
            [
                "--hybrid-agent",
                "--hitl",
                "--thread-id",
                "demo-1",
                "--resume-approval",
                "approve",
            ],
            "requires --approval-id",
        ),
    ],
)
async def test_cli_rejects_invalid_hitl_flag_combinations(arguments, message, capsys):
    exit_code = await cli_module._run(build_parser().parse_args(arguments))

    assert exit_code == 2
    assert message in capsys.readouterr().out


def test_resume_rejects_explicit_goal_arguments():
    args = build_parser().parse_args(
        [
            "--hybrid-agent",
            "--hitl",
            "--thread-id",
            "demo-1",
            "--resume-approval",
            "approve",
            "--approval-id",
            "approval-1",
            "--keyword",
            "replacement goal",
        ]
    )
    args._supplied_options = {"--keyword"}

    assert validate_hitl_args(args) == "--keyword cannot be used when resuming a HITL thread"


@pytest.mark.asyncio
async def test_cli_approve_resumes_checkpoint_to_completion(monkeypatch, tmp_path):
    states = []

    def fake_write_artifacts(state, **kwargs):
        states.append(state)
        return {}

    monkeypatch.setattr(cli_module, "write_hybrid_artifacts", fake_write_artifacts)
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    db_path = tmp_path / "jobs.sqlite"
    start_args = build_parser().parse_args(
        [
            "--demo",
            "--hybrid-agent",
            "--hitl",
            "--thread-id",
            "demo-approve",
            "--checkpoint-db",
            str(checkpoint_path),
            "--db-path",
            str(db_path),
            "--keyword",
            "AI intern",
            "--target-count",
            "1",
            "--agent-max-steps",
            "8",
        ]
    )

    assert await cli_module._run(start_args) == 0
    approval_id = states[-1].pending_approval.approval_id
    resume_args = build_parser().parse_args(
        [
            "--hybrid-agent",
            "--hitl",
            "--thread-id",
            "demo-approve",
            "--checkpoint-db",
            str(checkpoint_path),
            "--db-path",
            str(db_path),
            "--resume-approval",
            "approve",
            "--approval-id",
            approval_id,
            "--approval-note",
            "Reviewed summary",
        ]
    )

    assert await cli_module._run(resume_args) == 0
    assert states[-1].terminal_status == "completed"
    assert states[-1].terminal_reason == "target_reached"


@pytest.mark.asyncio
async def test_cli_runs_planner_benchmark_and_writes_artifacts(monkeypatch, capsys):
    captured = {}

    async def fake_run_planner_benchmark(*, providers, output_dir):
        captured["providers"] = providers
        captured["output_dir"] = output_dir
        return SimpleNamespace(
            providers=[
                SimpleNamespace(
                    provider="deterministic",
                    status="executed",
                    completed_cases=4,
                    total_cases=5,
                    terminated_cases=5,
                    fallback_rate=0.0,
                    total_tokens=0,
                    error="",
                )
            ]
        )

    def fake_write_artifacts(matrix, output_dir):
        captured["matrix"] = matrix
        captured["artifact_dir"] = output_dir
        return {
            "json": Path(output_dir) / "planner-benchmark.json",
            "markdown": Path(output_dir) / "planner-benchmark.md",
        }

    monkeypatch.setattr(
        cli_module,
        "run_planner_benchmark",
        fake_run_planner_benchmark,
    )
    monkeypatch.setattr(
        cli_module,
        "write_planner_benchmark_artifacts",
        fake_write_artifacts,
    )
    args = build_parser().parse_args(
        [
            "--agent-planner-benchmark",
            "--agent-planner-benchmark-providers",
            "deterministic,deepseek",
            "--agent-planner-benchmark-output-dir",
            "outputs/planner",
        ]
    )

    exit_code = await cli_module._run(args)
    output = capsys.readouterr().out

    assert exit_code == 0
    assert captured["providers"] == ["deterministic", "deepseek"]
    assert captured["output_dir"] == "outputs/planner"
    assert captured["artifact_dir"] == "outputs/planner"
    assert "Planner benchmark" in output
    assert "deterministic: executed" in output
    assert "4/5" in output
    assert "planner-benchmark.json" in output
