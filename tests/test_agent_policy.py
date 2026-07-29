from __future__ import annotations

from web_task_agent.agent_models import (
    AgentAction,
    AgentBudget,
    DecisionAgentState,
    DecisionSource,
    ToolObservation,
)
from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.models import JobPosting, UserProfile


def _state(**updates) -> DecisionAgentState:
    state = DecisionAgentState(
        user=UserProfile(keyword="AI intern", target_count=1),
        budget=AgentBudget(max_steps=6),
    )
    return state.model_copy(update=updates)


def _job(url: str = "https://example.com/jobs/1") -> JobPosting:
    return JobPosting(
        title="AI Engineering Intern",
        company="Example AI",
        location="Remote",
        source="fixture",
        url=url,
        confidence=0.9,
    )


def test_policy_stops_when_budget_is_exhausted():
    state = _state(budget=AgentBudget(max_steps=2, consumed_steps=2))

    decision = DeterministicAgentPolicy().decide(state)

    assert decision.action is AgentAction.FINISH
    assert decision.arguments["terminal_reason"] == "budget_exhausted"
    assert decision.source is DecisionSource.POLICY
    assert decision.reason


def test_policy_opens_next_candidate_after_recoverable_failure():
    failed = "https://example.com/jobs/broken"
    next_url = "https://example.com/jobs/working"
    state = _state(
        candidate_urls=[failed, next_url],
        current_url=failed,
        visited_urls={failed},
        retry_counts={failed: 2},
        last_observation=ToolObservation(
            tool_name=AgentAction.OPEN_PAGE,
            success=False,
            error_category="browser_error",
            error_message="navigation failed",
            recoverable=True,
        ),
    )

    decision = DeterministicAgentPolicy().decide(state)

    assert decision.action is AgentAction.OPEN_PAGE
    assert decision.target == next_url
    assert decision.reason


def test_policy_uses_visual_after_low_confidence_text_extraction():
    url = "https://example.com/jobs/1"
    state = _state(
        candidate_urls=[url],
        current_url=url,
        visual_available=True,
        last_observation=ToolObservation(
            tool_name=AgentAction.EXTRACT_TEXT,
            success=True,
            summary="weak extraction",
            payload={"confidence": 0.2},
        ),
    )

    decision = DeterministicAgentPolicy().decide(state)

    assert decision.action is AgentAction.EXTRACT_VISUAL
    assert decision.target == url
    assert decision.source is DecisionSource.POLICY


def test_policy_finishes_when_target_count_is_reached():
    state = _state(verified_jobs=[_job()])

    decision = DeterministicAgentPolicy().decide(state)

    assert decision.action is AgentAction.FINISH
    assert decision.arguments["terminal_reason"] == "target_reached"
    assert decision.reason


def test_policy_never_retries_a_url_more_than_twice():
    failed = "https://example.com/jobs/broken"
    next_url = "https://example.com/jobs/next"
    state = _state(
        candidate_urls=[failed, next_url],
        current_url=failed,
        visited_urls={failed},
        retry_counts={failed: 2},
        last_observation=ToolObservation(
            tool_name=AgentAction.OPEN_PAGE,
            success=False,
            error_category="timeout",
            error_message="timed out",
            recoverable=True,
        ),
    )

    decision = DeterministicAgentPolicy().decide(state)

    assert decision.target != failed
    assert decision.target == next_url


def test_policy_starts_with_search_when_no_candidates_exist():
    decision = DeterministicAgentPolicy().decide(_state())

    assert decision.action is AgentAction.SEARCH_JOBS
    assert decision.source is DecisionSource.POLICY

