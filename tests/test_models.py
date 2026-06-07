from datetime import datetime, timedelta, timezone

import pytest

from web_task_agent.models import (
    BrowserPage,
    JobPosting,
    RunMetrics,
    UserProfile,
    WorkflowState,
)


def test_job_posting_normalizes_skills_and_confidence():
    job = JobPosting(
        title="AI Engineering Intern",
        company="Example AI",
        location="Remote",
        source="fixture",
        url="https://example.com/jobs/1",
        requirements="Python, LLM, RAG",
        responsibilities="Build AI agents",
        skills=["Python", " python ", "LLM"],
        posted_at="2026-06-07",
        confidence=1.2,
    )

    assert job.skills == ["Python", "LLM"]
    assert job.confidence == 1.0


def test_job_posting_clamps_negative_confidence():
    job = JobPosting(
        title="AI Engineering Intern",
        company="Example AI",
        location="Remote",
        source="fixture",
        url="https://example.com/jobs/1",
        confidence=-0.3,
    )

    assert job.confidence == 0.0


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), float("-inf")])
def test_job_posting_rejects_non_finite_confidence(confidence):
    with pytest.raises(ValueError):
        JobPosting(
            title="AI Engineering Intern",
            company="Example AI",
            location="Remote",
            source="fixture",
            url="https://example.com/jobs/1",
            confidence=confidence,
        )


def test_run_metrics_normalizes_timezone_aware_datetimes_to_utc():
    local_timezone = timezone(timedelta(hours=8))
    started_at = datetime(2026, 6, 7, 10, 30, tzinfo=local_timezone)
    finished_at = datetime(2026, 6, 7, 11, 45, tzinfo=local_timezone)

    metrics = RunMetrics(run_id="run-1", started_at=started_at, finished_at=finished_at)

    assert metrics.started_at == datetime(2026, 6, 7, 2, 30, tzinfo=timezone.utc)
    assert metrics.finished_at == datetime(2026, 6, 7, 3, 45, tzinfo=timezone.utc)
    assert metrics.started_at.tzinfo == timezone.utc
    assert metrics.finished_at.tzinfo == timezone.utc


def test_run_metrics_rejects_naive_datetimes():
    with pytest.raises(ValueError):
        RunMetrics(run_id="run-1", started_at=datetime(2026, 6, 7, 10, 30))

    with pytest.raises(ValueError):
        RunMetrics(run_id="run-1", finished_at=datetime(2026, 6, 7, 11, 45))


@pytest.mark.parametrize(
    "field_name",
    ["pages_visited", "jobs_found", "valid_jobs", "duplicate_jobs", "failed_pages"],
)
def test_run_metrics_rejects_negative_integer_counters(field_name):
    with pytest.raises(ValueError):
        RunMetrics(run_id="run-1", **{field_name: -1})


@pytest.mark.parametrize("field_name", ["avg_steps_per_job", "estimated_token_cost"])
def test_run_metrics_rejects_negative_float_metrics(field_name):
    with pytest.raises(ValueError):
        RunMetrics(run_id="run-1", **{field_name: -0.1})


@pytest.mark.parametrize("field_name", ["avg_steps_per_job", "estimated_token_cost"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), "nan", "inf"])
def test_run_metrics_rejects_non_finite_float_metrics(field_name, value):
    with pytest.raises(ValueError):
        RunMetrics(run_id="run-1", **{field_name: value})


def test_user_profile_rejects_invalid_target_count():
    with pytest.raises(ValueError):
        UserProfile(keyword="AI intern", location="Remote", target_count=0)

    with pytest.raises(ValueError):
        UserProfile(keyword="AI intern", location="Remote", target_count=51)


def test_user_profile_rejects_empty_trimmed_keyword():
    with pytest.raises(ValueError):
        UserProfile(keyword="   ", location="Remote")


def test_workflow_state_mutable_defaults_are_isolated():
    user = UserProfile(keyword="AI intern", location="Remote")
    first = WorkflowState(user=user)
    second = WorkflowState(user=user)

    first.pages.append(
        BrowserPage(
            url="https://example.com/jobs",
            content="AI Engineering Intern at Example AI",
        )
    )
    first.jobs.append(
        JobPosting(
            title="AI Engineering Intern",
            company="Example AI",
            location="Remote",
            source="fixture",
            url="https://example.com/jobs/1",
        )
    )
    first.search_queries.append("AI intern Remote")
    first.candidate_urls.append("https://example.com/jobs/1")
    first.failed_urls.append("https://example.com/broken")

    assert second.pages == []
    assert second.jobs == []
    assert second.search_queries == []
    assert second.candidate_urls == []
    assert second.failed_urls == []


def test_workflow_state_tracks_pages_jobs_and_metrics():
    state = WorkflowState(
        user=UserProfile(
            keyword="AI intern",
            location="Remote",
            target_count=2,
            skills=["Python", "LangGraph"],
        )
    )
    state.pages.append(
        BrowserPage(
            url="https://example.com/jobs",
            title="Jobs",
            content="AI Engineering Intern at Example AI",
        )
    )
    state.jobs.append(
        JobPosting(
            title="AI Engineering Intern",
            company="Example AI",
            location="Remote",
            source="fixture",
            url="https://example.com/jobs/1",
            requirements="Python",
            responsibilities="Build agents",
            skills=["Python"],
        )
    )
    state.metrics = RunMetrics(run_id="run-1", pages_visited=1, jobs_found=1, valid_jobs=1)

    assert state.user.target_count == 2
    assert state.pages[0].title == "Jobs"
    assert state.jobs[0].company == "Example AI"
    assert state.metrics.valid_jobs == 1
