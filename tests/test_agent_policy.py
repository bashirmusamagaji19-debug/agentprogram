from __future__ import annotations

from web_task_agent.agent_models import (
    AgentAction,
    AgentBudget,
    DecisionAgentState,
    DecisionSource,
    ToolObservation,
)
from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.models import BrowserPage, JobPosting, MatchResult, UserProfile


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


def test_policy_scores_saves_then_finishes_when_target_count_is_reached():
    state = _state(verified_jobs=[_job()])

    decision = DeterministicAgentPolicy().decide(state)

    assert decision.action is AgentAction.SCORE_MATCH

    state.matches = [
        MatchResult(
            job_id=state.verified_jobs[0].url,
            score=0.9,
            matched_skills=["Python"],
        )
    ]
    decision = DeterministicAgentPolicy().decide(state)

    assert decision.action is AgentAction.SAVE_RESULTS

    state.saved = True
    decision = DeterministicAgentPolicy().decide(state)

    assert decision.action is AgentAction.FINISH
    assert decision.arguments["terminal_reason"] == "target_reached"
    assert decision.reason


def test_policy_does_not_claim_target_reached_when_budget_expires_before_save():
    state = _state(
        verified_jobs=[_job()],
        budget=AgentBudget(max_steps=2, consumed_steps=2),
    )

    decision = DeterministicAgentPolicy().decide(state)

    assert decision.action is AgentAction.FINISH
    assert decision.arguments["terminal_reason"] == "budget_exhausted"


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


def test_policy_routes_verifier_rejection_to_visual_recovery():
    url = "https://example.com/jobs/1"
    state = _state(
        candidate_urls=[url],
        current_url=url,
        extracted_jobs=[_job(url)],
        visual_available=True,
        observation_history=[
            ToolObservation(
                tool_name=AgentAction.EXTRACT_TEXT,
                success=True,
                payload={"confidence": 0.4},
            )
        ],
        last_observation=ToolObservation(
            tool_name=AgentAction.VERIFY_JOB,
            success=False,
            error_category="verification_filtered",
            error_message="missing requirements",
            recoverable=True,
        ),
    )

    decision = DeterministicAgentPolicy().decide(state)

    assert decision.action is AgentAction.EXTRACT_VISUAL
    assert decision.target == url


def test_policy_moves_to_next_candidate_when_verifier_cannot_recover():
    current = "https://example.com/jobs/1"
    next_url = "https://example.com/jobs/2"
    state = _state(
        candidate_urls=[current, next_url],
        current_url=current,
        visited_urls={current},
        extracted_jobs=[_job(current)],
        visual_available=False,
        last_observation=ToolObservation(
            tool_name=AgentAction.VERIFY_JOB,
            success=False,
            error_category="verification_filtered",
            error_message="not relevant",
            recoverable=True,
        ),
    )

    decision = DeterministicAgentPolicy().decide(state)

    assert decision.action is AgentAction.OPEN_PAGE
    assert decision.target == next_url


def test_policy_extracts_new_current_page_instead_of_reusing_previous_job():
    previous_url = "https://example.com/jobs/rejected"
    current_url = "https://example.com/jobs/valid"
    state = _state(
        candidate_urls=[previous_url, current_url],
        current_url=current_url,
        current_page=BrowserPage(
            url=current_url,
            title="AI Intern",
            content="Title: AI Intern",
        ),
        visited_urls={previous_url, current_url},
        extracted_jobs=[_job(previous_url)],
        last_observation=ToolObservation(
            tool_name=AgentAction.OPEN_PAGE,
            success=True,
            summary="opened next candidate",
        ),
    )

    decision = DeterministicAgentPolicy().decide(state)

    assert decision.action is AgentAction.EXTRACT_TEXT
    assert decision.target == current_url
