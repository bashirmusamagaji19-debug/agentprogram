from pathlib import Path

import pytest

from tests.fixtures.job_pages import FAKE_JOB_PAGES
from web_task_agent.browser import FakeBrowserClient
from web_task_agent.extractor import PageExtractor
from web_task_agent.matcher import JobMatcher
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
        matcher=JobMatcher(),
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
    assert len(state.matches) == state.metrics.valid_jobs
    assert state.matches[0].score > 0
    assert state.report_path is not None
    assert "run-test.md" in state.report_path
    report = Path(state.report_path).read_text(encoding="utf-8")
    assert "匹配分析" in report
    assert "编排模式: sequential" in report
    assert "## Agent 执行轨迹" in report
    assert state.metadata["orchestration_mode"] == "sequential"
    assert [item["node"] for item in state.metadata["execution_trace"]] == [
        "planner",
        "browser",
        "extractor",
        "verifier",
        "matcher",
        "reporter",
    ]


@pytest.mark.asyncio
async def test_workflow_opens_seed_urls_without_searching(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()
    opened_urls: list[str] = []
    searched_queries: list[str] = []

    class SeedBrowser:
        async def search(self, query: str, target_count: int) -> list[object]:
            searched_queries.append(query)
            return []

        async def open_url(self, url: str) -> object:
            opened_urls.append(url)
            return FAKE_JOB_PAGES[0].model_copy(update={"url": url})

    workflow = WebTaskWorkflow(
        browser=SeedBrowser(),
        extractor=PageExtractor(),
        matcher=JobMatcher(),
        verifier=JobVerifier(required_keywords=["AI", "LLM", "Agent"]),
        repository=repo,
        reporter=MarkdownReporter(output_dir=tmp_path / "reports"),
    )

    state = await workflow.run(
        UserProfile(
            keyword="AI intern",
            location="Remote",
            target_count=2,
            skills=["Python"],
            seed_urls=["https://example.com/jobs/seed"],
        ),
        run_id="run-seed",
    )

    assert searched_queries == []
    assert opened_urls == ["https://example.com/jobs/seed"]
    assert state.candidate_urls == ["https://example.com/jobs/seed"]
    assert state.metrics is not None
    assert state.metrics.pages_visited == 1


@pytest.mark.asyncio
async def test_workflow_records_seed_url_error_details(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()

    class FailingSeedBrowser:
        async def search(self, query: str, target_count: int) -> list[object]:
            raise AssertionError("search should not be called")

        async def open_url(self, url: str) -> object:
            raise ValueError("fixture page missing")

    workflow = WebTaskWorkflow(
        browser=FailingSeedBrowser(),
        extractor=PageExtractor(),
        matcher=JobMatcher(),
        verifier=JobVerifier(required_keywords=["AI", "LLM", "Agent"]),
        repository=repo,
        reporter=MarkdownReporter(output_dir=tmp_path / "reports"),
    )

    state = await workflow.run(
        UserProfile(
            keyword="AI intern",
            target_count=1,
            seed_urls=["https://example.com/jobs/missing"],
        ),
        run_id="run-seed-failure",
    )

    assert state.failed_urls == ["https://example.com/jobs/missing"]
    assert state.metadata["failed_url_errors"] == [
        {
            "url": "https://example.com/jobs/missing",
            "error": "ValueError: fixture page missing",
        }
    ]


@pytest.mark.asyncio
async def test_workflow_records_no_valid_jobs_without_crashing(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()
    workflow = WebTaskWorkflow(
        browser=FakeBrowserClient(FAKE_JOB_PAGES),
        extractor=PageExtractor(),
        matcher=JobMatcher(),
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


def test_workflow_builds_langgraph_with_named_agent_nodes(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()
    workflow = WebTaskWorkflow(
        browser=FakeBrowserClient(FAKE_JOB_PAGES),
        extractor=PageExtractor(),
        matcher=JobMatcher(),
        verifier=JobVerifier(required_keywords=["AI", "LLM", "Agent"]),
        repository=repo,
        reporter=MarkdownReporter(output_dir=tmp_path / "reports"),
    )

    graph = workflow.build_langgraph()
    graph_dict = graph.get_graph().to_json()
    node_ids = {node["id"] for node in graph_dict["nodes"]}

    assert {
        "planner",
        "browser",
        "extractor",
        "verifier",
        "matcher",
        "reporter",
    }.issubset(node_ids)


@pytest.mark.asyncio
async def test_workflow_runs_end_to_end_with_langgraph(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()
    workflow = WebTaskWorkflow(
        browser=FakeBrowserClient(FAKE_JOB_PAGES),
        extractor=PageExtractor(),
        matcher=JobMatcher(),
        verifier=JobVerifier(required_keywords=["AI", "LLM", "Agent"]),
        repository=repo,
        reporter=MarkdownReporter(output_dir=tmp_path / "reports"),
    )

    state = await workflow.run_with_langgraph(
        UserProfile(
            keyword="AI intern",
            location="Remote",
            target_count=2,
            skills=["Python", "LangGraph"],
        ),
        run_id="run-langgraph",
    )

    assert state.metrics is not None
    assert state.metrics.valid_jobs >= 1
    assert state.matches
    assert state.report_path is not None
    assert "run-langgraph.md" in state.report_path
    assert state.metadata["orchestration_mode"] == "langgraph"
    assert [item["node"] for item in state.metadata["execution_trace"]] == [
        "planner",
        "browser",
        "extractor",
        "verifier",
        "matcher",
        "reporter",
    ]
