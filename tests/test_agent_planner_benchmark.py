from __future__ import annotations

import json

import pytest

from web_task_agent.agent_models import AgentBudget, AgentMetrics, DecisionAgentState
from web_task_agent.agent_planner import PlannerTelemetry
from web_task_agent.agent_planner_benchmark import (
    PlannerBenchmarkCaseResult,
    PlannerBenchmarkMatrix,
    build_planner_benchmark_scenarios,
    parse_planner_benchmark_providers,
    render_planner_benchmark_markdown,
    run_planner_benchmark,
    summarize_planner_provider,
    write_planner_benchmark_artifacts,
)
from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.models import UserProfile


def _state(
    *,
    status: str,
    reason: str,
    consumed_steps: int,
    metrics: AgentMetrics,
) -> DecisionAgentState:
    return DecisionAgentState(
        user=UserProfile(keyword="AI intern", target_count=1),
        budget=AgentBudget(max_steps=8, consumed_steps=consumed_steps),
        metrics=metrics,
        terminal_status=status,
        terminal_reason=reason,
    )


def test_parse_planner_benchmark_providers_defaults_dedupes_and_validates():
    assert parse_planner_benchmark_providers(None) == [
        "deterministic",
        "deepseek",
        "qwen",
    ]
    assert parse_planner_benchmark_providers("qwen, deepseek,qwen") == [
        "qwen",
        "deepseek",
    ]

    with pytest.raises(ValueError, match="Unsupported planner benchmark provider"):
        parse_planner_benchmark_providers("deterministic,unknown")


def test_summarize_planner_provider_separates_completion_from_termination():
    completed = PlannerBenchmarkCaseResult.from_state(
        case_id="completed",
        scenario="Target reached",
        state=_state(
            status="completed",
            reason="target_reached",
            consumed_steps=4,
            metrics=AgentMetrics(
                tool_calls=5,
                successful_tool_calls=5,
                recovery_attempts=1,
                successful_recoveries=1,
                planner_calls=3,
                total_latency_ms=40,
            ),
        ),
        runtime_latency_ms=60,
    )
    exhausted = PlannerBenchmarkCaseResult.from_state(
        case_id="exhausted",
        scenario="Budget exhausted",
        state=_state(
            status="partial",
            reason="budget_exhausted",
            consumed_steps=8,
            metrics=AgentMetrics(
                tool_calls=9,
                successful_tool_calls=4,
                recovery_attempts=2,
                successful_recoveries=1,
                planner_calls=2,
                fallback_decisions=1,
                invalid_actions=1,
                total_latency_ms=80,
            ),
        ),
        runtime_latency_ms=100,
    )

    result = summarize_planner_provider(
        provider="deepseek",
        model="deepseek-chat",
        case_results=[completed, exhausted],
        telemetry=PlannerTelemetry(
            calls=5,
            successful_calls=4,
            failed_calls=1,
            total_latency_ms=50,
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
        ),
    )

    assert result.status == "executed"
    assert result.total_cases == 2
    assert result.completed_cases == 1
    assert result.task_completion_rate == 0.5
    assert result.terminated_cases == 2
    assert result.loop_termination_rate == 1.0
    assert result.tool_success_rate == 9 / 14
    assert result.recovery_success_rate == 2 / 3
    assert result.invalid_action_rate == 1 / 5
    assert result.fallback_rate == 1 / 5
    assert result.average_steps == 6
    assert result.max_steps == 8
    assert result.runtime_latency_ms == 160
    assert result.planner_latency_ms == 50
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 20
    assert result.total_tokens == 120


class PolicyMirroringPlanner:
    def __init__(self, provider: str):
        self.provider = provider
        self.model = f"{provider}-test"
        self.telemetry = PlannerTelemetry()
        self.policy = DeterministicAgentPolicy()

    async def decide(self, state):
        self.telemetry.calls += 1
        self.telemetry.successful_calls += 1
        self.telemetry.prompt_tokens += 10
        self.telemetry.completion_tokens += 2
        self.telemetry.total_tokens += 12
        return self.policy.decide(state)


def test_planner_benchmark_catalog_has_five_versioned_controlled_scenarios():
    scenarios = build_planner_benchmark_scenarios()

    assert [scenario.case_id for scenario in scenarios] == [
        "seed-happy-path",
        "search-happy-path",
        "open-recovery",
        "verifier-recovery",
        "budget-exhaustion",
    ]
    assert all(scenario.scenario.strip() for scenario in scenarios)
    assert all(scenario.max_steps >= 1 for scenario in scenarios)


@pytest.mark.asyncio
async def test_run_planner_benchmark_executes_identical_cases_for_each_provider(tmp_path):
    created = []

    def planner_factory(provider: str):
        planner = PolicyMirroringPlanner(provider)
        created.append(planner)
        return planner

    matrix = await run_planner_benchmark(
        providers=["deterministic", "deepseek"],
        output_dir=tmp_path,
        planner_factory=planner_factory,
    )

    assert isinstance(matrix, PlannerBenchmarkMatrix)
    assert matrix.benchmark_version == "hybrid-agent-planner-controlled-v1"
    assert [provider.provider for provider in matrix.providers] == [
        "deterministic",
        "deepseek",
    ]
    assert all(provider.status == "executed" for provider in matrix.providers)
    assert all(provider.total_cases == 5 for provider in matrix.providers)
    assert all(provider.completed_cases == 4 for provider in matrix.providers)
    assert all(provider.terminated_cases == 5 for provider in matrix.providers)
    assert matrix.providers[0].planner_calls == 0
    assert matrix.providers[1].planner_calls > 0
    assert len(created) == 1
    assert not (tmp_path / "_runs").exists()


@pytest.mark.asyncio
async def test_run_planner_benchmark_skips_unconfigured_provider_without_aborting(tmp_path):
    def planner_factory(provider: str):
        raise RuntimeError(f"{provider} API key is missing")

    matrix = await run_planner_benchmark(
        providers=["deterministic", "qwen"],
        output_dir=tmp_path,
        planner_factory=planner_factory,
    )

    deterministic, qwen = matrix.providers
    assert deterministic.status == "executed"
    assert deterministic.total_cases == 5
    assert qwen.status == "skipped"
    assert qwen.total_cases == 0
    assert qwen.error == "qwen API key is missing"


@pytest.mark.asyncio
async def test_planner_benchmark_artifacts_explain_scope_and_exclude_secrets(tmp_path):
    matrix = await run_planner_benchmark(
        providers=["deterministic"],
        output_dir=tmp_path / "runs",
    )

    markdown = render_planner_benchmark_markdown(matrix)
    paths = write_planner_benchmark_artifacts(matrix, tmp_path / "public")
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "真实 Planner 对照评测" in markdown
    assert "受控、可复现" in markdown
    assert "任务完成率" in markdown
    assert "循环终止率" in markdown
    assert "Token" in markdown
    assert "面试表达" in markdown
    assert payload["benchmark_version"] == "hybrid-agent-planner-controlled-v1"
    assert payload["scope"] == "controlled replayable runtime scenarios"
    assert payload["providers"][0]["cases"][0]["terminal_reason"] == "target_reached"
    assert paths["markdown"].read_text(encoding="utf-8") == markdown
    for forbidden in [
        "Authorization",
        "Bearer ",
        "PRIVATE RESUME CONTENT",
        "api_key",
        "messages",
        "response_content",
    ]:
        assert forbidden not in serialized
