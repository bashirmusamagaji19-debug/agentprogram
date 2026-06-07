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
