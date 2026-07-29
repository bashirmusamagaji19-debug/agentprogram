from __future__ import annotations

import pytest
from pydantic import ValidationError

from web_task_agent.agent_models import (
    AgentAction,
    AgentBudget,
    AgentDecision,
    AgentMetrics,
    DecisionAgentState,
    ToolObservation,
)
from web_task_agent.models import UserProfile


def test_decision_rejects_unknown_action():
    with pytest.raises(ValidationError):
        AgentDecision(action="delete_files", reason="unsafe action")


def test_decision_requires_reason_and_bounded_confidence():
    with pytest.raises(ValidationError):
        AgentDecision(action=AgentAction.SEARCH_JOBS, reason="   ")

    with pytest.raises(ValidationError):
        AgentDecision(
            action=AgentAction.SEARCH_JOBS,
            reason="search for candidates",
            confidence=1.1,
        )


def test_observation_requires_error_category_for_failure():
    with pytest.raises(ValidationError):
        ToolObservation(tool_name=AgentAction.OPEN_PAGE, success=False)


def test_budget_consumes_steps_without_becoming_negative():
    budget = AgentBudget(max_steps=2)

    first = budget.consume()
    second = first.consume()
    exhausted = second.consume()

    assert budget.remaining_steps == 2
    assert first.remaining_steps == 1
    assert second.remaining_steps == 0
    assert exhausted.remaining_steps == 0
    assert exhausted.exhausted is True


def test_agent_metrics_records_recovery_and_fallback_rates():
    metrics = AgentMetrics(
        tool_calls=4,
        successful_tool_calls=3,
        recovery_attempts=2,
        successful_recoveries=1,
        planner_calls=2,
        fallback_decisions=1,
        invalid_actions=1,
        total_latency_ms=120,
    )

    assert metrics.tool_success_rate == 0.75
    assert metrics.recovery_success_rate == 0.5
    assert metrics.fallback_rate == 0.5
    assert metrics.average_tool_latency_ms == 30.0


def test_decision_agent_state_mutable_defaults_are_isolated():
    user = UserProfile(keyword="AI intern")
    first = DecisionAgentState(user=user, budget=AgentBudget(max_steps=3))
    second = DecisionAgentState(user=user, budget=AgentBudget(max_steps=3))

    first.candidate_urls.append("https://example.com/jobs/1")
    first.retry_counts["https://example.com/jobs/1"] = 1
    first.metrics.tool_calls = 1

    assert second.candidate_urls == []
    assert second.retry_counts == {}
    assert second.metrics.tool_calls == 0

