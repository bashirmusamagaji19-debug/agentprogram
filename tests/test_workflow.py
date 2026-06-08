import pytest

from tests.fixtures.job_pages import FAKE_JOB_PAGES
from web_task_agent.browser import FakeBrowserClient
from web_task_agent.extractor import PageExtractor
from web_task_agent.models import UserProfile
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow


@pytest.mark.asyncio
async def test_workflow_runs_end_to_end_with_fake_browser(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()
    workflow = WebTaskWorkflow(
        browser=FakeBrowserClient(FAKE_JOB_PAGES),
        extractor=PageExtractor(),
        verifier=JobVerifier(required_keywords=["AI", "LLM", "Agent"]),
        repository=repo,
        reporter=MarkdownReporter(output_dir=tmp_path / "reports"),
    )

    state = await workflow.run(
        UserProfile(
            keyword="AI intern",
            location="Remote",
            target_count=2,
            skills=["Python", "LangGraph"],
        ),
        run_id="run-test",
    )

    assert state.metrics is not None
    assert state.metrics.pages_visited == 2
    assert state.metrics.valid_jobs >= 1
    assert len(repo.list_jobs()) >= 1
    assert state.report_path is not None
    assert "run-test.md" in state.report_path


@pytest.mark.asyncio
async def test_workflow_records_no_valid_jobs_without_crashing(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()
    workflow = WebTaskWorkflow(
        browser=FakeBrowserClient(FAKE_JOB_PAGES),
        extractor=PageExtractor(),
        verifier=JobVerifier(required_keywords=["quantum"]),
        repository=repo,
        reporter=MarkdownReporter(output_dir=tmp_path / "reports"),
    )

    state = await workflow.run(
        UserProfile(keyword="AI intern", location="Remote", target_count=1),
        run_id="run-empty",
    )

    assert state.metrics is not None
    assert state.metrics.jobs_found == 1
    assert state.metrics.valid_jobs == 0
    assert state.metrics.avg_steps_per_job == 0.0
    assert repo.list_jobs() == []
    assert state.report_path is not None
