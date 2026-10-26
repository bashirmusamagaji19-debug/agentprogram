from __future__ import annotations

import pytest

from web_task_agent.agent_cli import build_hybrid_runtime
from web_task_agent.agent_models import AgentAction, AgentBudget, DecisionAgentState
from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.agent_tools import OpenPageTool, VerifyJobTool
from web_task_agent.models import BrowserPage, JobPosting, UserProfile
from web_task_agent.page_reliability import PageRemovedError
from web_task_agent.verifier import JobVerifier

from .test_agent_multitarget import make_workflow


@pytest.mark.asyncio
@pytest.mark.parametrize(("location", "requirements", "accepted"), [
    ("Shanghai", "Python LangGraph AI", False),
    ("Unknown", "Python LangGraph AI", False),
    ("Beijing", "Python AI", False),
    ("Beijing / Shanghai", "Python LangGraph AI", True),
])
async def test_strict_verifier_preserves_all_keywords_and_any_city(location, requirements, accepted):
    state = DecisionAgentState(user=UserProfile(keyword="Python LangGraph", location="北京", target_count=1))
    state.current_url = "https://example.com/jobs/1"
    state.extracted_jobs = [JobPosting(
        url=state.current_url, title="AI Intern", company="Example", location=location,
        source="fixture", requirements=requirements, responsibilities="Build AI agents", confidence=1.0,
    )]
    observation = await VerifyJobTool(JobVerifier(), enforce_search_conditions=True).execute(state, {})
    assert observation.success is accepted
    assert bool(state.verified_jobs) is accepted


@pytest.mark.asyncio
async def test_removed_page_is_not_retryable_or_leaked_into_error_text():
    class Browser:
        async def open_url(self, url):
            raise PageRemovedError("private credential must not be logged")

    state = DecisionAgentState(user=UserProfile(keyword="AI", target_count=1))
    observation = await OpenPageTool(Browser()).execute(state, {"url": "https://example.com/jobs/1"})
    assert not observation.success
    assert not observation.recoverable
    assert observation.error_category == "removed"
    assert "credential" not in observation.error_message


def test_exhausted_failed_unvisited_url_is_not_selected_again():
    state = DecisionAgentState(
        user=UserProfile(keyword="AI", target_count=2),
        candidate_urls=["https://example.com/jobs/1"],
        retry_counts={"https://example.com/jobs/1": 2},
    )
    assert DeterministicAgentPolicy()._next_candidate(state) is None


@pytest.mark.asyncio
async def test_goal_saved_on_last_step_is_completed(tmp_path):
    workflow, pages = make_workflow(tmp_path, count=1)
    state = DecisionAgentState(
        user=UserProfile(keyword="AI intern", location="Remote", target_count=1),
        candidate_urls=[pages[0].url], budget=AgentBudget(max_steps=5),
    )
    result = await build_hybrid_runtime(workflow).run(state)
    assert result.saved
    assert result.terminal_reason == "target_reached"
    assert result.terminal_status == "completed"


@pytest.mark.asyncio
async def test_switching_page_clears_previous_content_even_on_failure():
    class Browser:
        async def open_url(self, url):
            raise TimeoutError()

    state = DecisionAgentState(
        user=UserProfile(keyword="AI"),
        current_page=BrowserPage(url="https://example.com/jobs/old", content="old"),
    )
    await OpenPageTool(Browser()).execute(state, {"url": "https://example.com/jobs/new"})
    assert state.current_page is None
    assert state.current_url.endswith("/new")
    assert state.last_decision is None


def test_retry_limit_respects_policy_configuration():
    state = DecisionAgentState(
        user=UserProfile(keyword="AI"), candidate_urls=["https://example.com/jobs/1"],
        retry_counts={"https://example.com/jobs/1": 1},
    )
    assert DeterministicAgentPolicy(max_url_attempts=1)._next_candidate(state) is None
    assert DeterministicAgentPolicy(max_url_attempts=3)._next_candidate(state) == state.candidate_urls[0]


def test_visual_attempts_are_scoped_to_current_page():
    state = DecisionAgentState(
        user=UserProfile(keyword="AI"), current_url="https://example.com/jobs/2",
        page_attempts={"https://example.com/jobs/1": {"extract_text": 1, "extract_visual": 1}},
        visual_available=True,
    )
    from web_task_agent.agent_models import ToolObservation
    state.last_observation = ToolObservation(
        tool_name=AgentAction.EXTRACT_TEXT, success=False,
        error_category="text_extraction_error", recoverable=True,
    )
    decision = DeterministicAgentPolicy().decide(state)
    assert decision.action is AgentAction.EXTRACT_VISUAL


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "category"), [(429, "blocked"), (503, "transient")])
async def test_http_failure_preserves_classification_and_safe_status(status, category):
    from web_task_agent.browser import PageHttpError

    class Browser:
        async def open_url(self, url):
            raise PageHttpError("credential=private", status_code=status)

    state = DecisionAgentState(user=UserProfile(keyword="AI"))
    observation = await OpenPageTool(Browser()).execute(state, {"url": "https://example.com/jobs/1"})
    assert observation.error_category == category
    assert observation.error_message == f"HTTP {status}"


@pytest.mark.asyncio
async def test_explicit_verifier_rejection_without_reasons_is_still_rejected():
    from web_task_agent.verifier import VerificationResult

    class RejectingVerifier:
        def verify(self, job):
            return VerificationResult(is_valid=False, reasons=[])

    state = DecisionAgentState(user=UserProfile(keyword="AI"), extracted_jobs=[JobPosting(
        title="AI Intern", company="Example", location="Remote", source="fixture",
        url="https://example.com/jobs/1", confidence=1.0,
    )])
    result = await VerifyJobTool(RejectingVerifier()).execute(state, {})
    assert not result.success
    assert not state.verified_jobs
    assert result.payload["reasons"]


@pytest.mark.asyncio
async def test_registry_does_not_export_arbitrary_exception_text():
    from web_task_agent.agent_models import AgentDecision
    from web_task_agent.agent_tools import AgentToolRegistry

    class Tool:
        name = AgentAction.VERIFY_JOB

        async def execute(self, state, arguments):
            raise ValueError("credential=secret")

    observation = await AgentToolRegistry([Tool()]).execute(
        AgentDecision(action=AgentAction.VERIFY_JOB, reason="verify"),
        DecisionAgentState(user=UserProfile(keyword="AI")),
    )
    assert "secret" not in observation.error_message


@pytest.mark.asyncio
async def test_ai_company_hr_role_does_not_become_a_technical_agent_job():
    state = DecisionAgentState(user=UserProfile(keyword="AI Agent 实习", location="北京", target_count=1))
    state.extracted_jobs = [JobPosting(
        title="AI 公司 HR 实习生", company="Example AI", location="北京", source="fixture",
        url="https://example.com/jobs/hr", requirements="熟悉 AI Agent 行业招聘。",
        responsibilities="招聘 AI Agent 工程师、安排面试。", confidence=1.0,
    )]
    observation = await VerifyJobTool(JobVerifier(), enforce_search_conditions=True).execute(state, {})
    assert not observation.success
    assert "nontechnical_role" in observation.payload["reasons"]
