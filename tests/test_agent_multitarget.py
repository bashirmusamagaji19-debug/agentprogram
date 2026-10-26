from __future__ import annotations

import pytest
from pydantic import ValidationError

from web_task_agent.agent_cli import build_hybrid_runtime
from web_task_agent.agent_models import AgentBudget
from web_task_agent.browser import FakeBrowserClient
from web_task_agent.extractor import PageExtractor
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import BrowserPage, UserProfile
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow


def make_workflow(tmp_path, count: int = 3):
    pages = [BrowserPage(
        url=f"https://example.com/jobs/{index}",
        title=f"AI Agent Intern {index}",
        content=(f"Title: AI Agent Intern {index}\nCompany: Example AI\n"
                 "Location: Remote\nRequirements: Python LangGraph AI\n"
                 "Responsibilities: Build and test AI agents with Python"),
    ) for index in range(1, count + 1)]
    repository = JobRepository(tmp_path / "jobs.sqlite")
    repository.initialize()
    return WebTaskWorkflow(
        browser=FakeBrowserClient(pages), extractor=PageExtractor(),
        matcher=JobMatcher(), verifier=JobVerifier(), repository=repository,
        reporter=MarkdownReporter(tmp_path / "reports"), enforce_search_conditions=True,
    ), pages


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [1, 2, 3])
async def test_runtime_collects_requested_number(tmp_path, target):
    workflow, pages = make_workflow(tmp_path)
    state = await workflow.run_with_hybrid_agent(
        UserProfile(keyword="AI intern", location="Remote", target_count=target,
                    seed_urls=[page.url for page in pages]),
        runtime=build_hybrid_runtime(workflow), max_steps=30,
    )
    assert len(state.verified_jobs) == target
    assert len(state.matches) == target
    assert len(workflow.repository.list_jobs()) == target
    assert state.terminal_status == "completed"
    assert state.terminal_reason == "target_reached"


@pytest.mark.asyncio
async def test_exhausted_candidates_save_partial_without_claiming_completion(tmp_path):
    workflow, pages = make_workflow(tmp_path, count=1)
    state = await workflow.run_with_hybrid_agent(
        UserProfile(keyword="AI intern", location="Remote", target_count=3,
                    seed_urls=[page.url for page in pages]),
        runtime=build_hybrid_runtime(workflow), max_steps=30,
    )
    assert len(workflow.repository.list_jobs()) == 1
    assert state.terminal_status == "partial"
    assert state.terminal_reason == "candidates_exhausted"


@pytest.mark.asyncio
async def test_empty_search_terminates_without_repeated_search_or_save(tmp_path):
    workflow, _ = make_workflow(tmp_path, count=0)
    state = await workflow.run_with_hybrid_agent(
        UserProfile(keyword="AI intern", location="Remote", target_count=3),
        runtime=build_hybrid_runtime(workflow), max_steps=30,
    )
    assert not workflow.repository.list_jobs()
    assert [decision.action.value for decision in state.decision_history] == ["search_jobs", "finish"]
    assert state.terminal_status == "failed"
    assert state.terminal_reason == "no_candidates"


def test_step_budget_accepts_400_and_rejects_401():
    assert AgentBudget(max_steps=400).remaining_steps == 400
    with pytest.raises(ValidationError):
        AgentBudget(max_steps=401)


@pytest.mark.asyncio
async def test_canonical_url_can_differ_from_requested_url(tmp_path):
    workflow, pages = make_workflow(tmp_path, count=1)
    canonical = "https://canonical.example.com/jobs/1"
    pages[0].metadata["canonical_url"] = canonical
    workflow.browser = FakeBrowserClient(pages)
    state = await workflow.run_with_hybrid_agent(
        UserProfile(keyword="AI intern", location="Remote", target_count=1,
                    seed_urls=[pages[0].url]),
        runtime=build_hybrid_runtime(workflow), max_steps=15,
    )
    assert state.terminal_reason == "target_reached"
    assert state.verified_jobs[0].url == canonical
    assert len([d for d in state.decision_history if d.action.value == "extract_text"]) == 1


@pytest.mark.asyncio
async def test_tracking_url_duplicate_does_not_fill_target_quota(tmp_path):
    workflow, pages = make_workflow(tmp_path, count=1)
    pages.append(pages[0].model_copy(update={"url": pages[0].url + "?utm_source=other"}))
    workflow.browser = FakeBrowserClient(pages)
    state = await workflow.run_with_hybrid_agent(
        UserProfile(keyword="AI intern", location="Remote", target_count=2,
                    seed_urls=[p.url for p in pages]),
        runtime=build_hybrid_runtime(workflow), max_steps=30,
    )
    assert len(state.verified_jobs) == 1
    assert len(workflow.repository.list_jobs()) == 1
    assert state.terminal_status == "partial"
    assert state.terminal_reason == "candidates_exhausted"


@pytest.mark.asyncio
async def test_all_removed_candidates_are_failed_not_partial(tmp_path):
    from web_task_agent.page_reliability import PageRemovedError

    class RemovedBrowser:
        async def open_url(self, url):
            raise PageRemovedError()

    workflow, _ = make_workflow(tmp_path, count=0)
    workflow.browser = RemovedBrowser()
    state = await workflow.run_with_hybrid_agent(
        UserProfile(keyword="AI intern", target_count=2, seed_urls=["https://example.com/jobs/removed"]),
        runtime=build_hybrid_runtime(workflow), max_steps=15,
    )
    assert state.terminal_status == "failed"
    assert state.terminal_reason == "candidates_exhausted"
    assert not workflow.repository.list_jobs()


@pytest.mark.asyncio
@pytest.mark.parametrize("canonical_metadata", [False, True])
async def test_one_canonical_job_with_location_variants_is_not_two_jobs(tmp_path, canonical_metadata):
    workflow, pages = make_workflow(tmp_path, count=1)
    pages.append(pages[0].model_copy(update={
        "url": pages[0].url + "?utm_source=other",
        "content": pages[0].content.replace("Location: Remote", "Location: Remote / US"),
    }))
    if canonical_metadata:
        for page in pages:
            page.metadata = {"canonical_url": "https://jobs.example.com/roles/42"}
    workflow.browser = FakeBrowserClient(pages)
    state = await workflow.run_with_hybrid_agent(
        UserProfile(keyword="AI intern", location="Remote", target_count=2, seed_urls=[p.url for p in pages]),
        runtime=build_hybrid_runtime(workflow), max_steps=30,
    )
    assert len(state.verified_jobs) == 1
    assert state.terminal_status == "partial"
    assert "US" in state.verified_jobs[0].location


@pytest.mark.asyncio
async def test_transient_search_error_is_not_mislabeled_as_empty_search(tmp_path):
    class BrokenSearch:
        calls = 0

        async def search(self, query, target_count):
            self.calls += 1
            raise TimeoutError()

    workflow, _ = make_workflow(tmp_path, count=0)
    browser = BrokenSearch()
    workflow.browser = browser
    state = await workflow.run_with_hybrid_agent(
        UserProfile(keyword="AI intern", target_count=1),
        runtime=build_hybrid_runtime(workflow), max_steps=10,
    )
    assert browser.calls == 2
    assert state.terminal_status == "failed"
    assert state.terminal_reason == "search_error"
