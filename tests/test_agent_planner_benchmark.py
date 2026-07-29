from __future__ import annotations

import pytest

from web_task_agent.agent_models import AgentBudget, AgentMetrics, DecisionAgentState
from web_task_agent.agent_planner import PlannerTelemetry
from web_task_agent.agent_planner_benchmark import (
    PlannerBenchmarkCaseResult,
    parse_planner_benchmark_providers,
    summarize_planner_provider,
)
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
