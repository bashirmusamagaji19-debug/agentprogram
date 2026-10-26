import pytest

from tests.fixtures.job_pages import FAKE_JOB_PAGES
from tests.fixtures.link_receipts import VerifiedLinkStub
from web_task_agent.agent_models import DecisionAgentState
from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.agent_runtime import HybridAgentRuntime
from web_task_agent.agent_tools import AgentToolRegistry, FinishTool
from web_task_agent.extractor import PageExtractor
from web_task_agent.models import UserProfile
from web_task_agent.storage import JobRepository
from web_task_agent.streamlit_runner import UiRunRequest, run_ui_request


@pytest.mark.asyncio
async def test_storage_failure_never_becomes_delivered_results(tmp_path, monkeypatch):
    async def load(url):
        return FAKE_JOB_PAGES[0].model_copy(update={"url": url})

    def broken_save(*args, **kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr(JobRepository, "save_jobs_once", broken_save)
    result = await run_ui_request(
        UiRunRequest(data_mode="seed_urls", keyword="AI intern", location="Remote", target_count=1,
                     engine="hybrid", seed_urls=[FAKE_JOB_PAGES[0].url]),
        output_root=tmp_path, environ={}, page_loader=load, link_verifier=VerifiedLinkStub())
    assert result.run_status == "failed"
    assert result.progress["verified"] == 1
    assert result.progress["delivered"] == 0
    assert result.jobs == [] and len(result.pending_jobs) == 1
    assert not result.saved
    assert not JobRepository(tmp_path / result.run_id / "agent.db").list_jobs()


@pytest.mark.asyncio
async def test_unsaved_verified_jobs_do_not_make_a_partial_delivery():
    state = DecisionAgentState(user=UserProfile(keyword="AI intern"),
                               verified_jobs=[PageExtractor().extract(FAKE_JOB_PAGES[0])])
    await FinishTool().execute(state, {"terminal_reason": "storage_error"})
    assert state.terminal_status == "failed"


def test_deadline_without_persistence_has_no_delivery():
    state = DecisionAgentState(user=UserProfile(keyword="AI intern"),
                               verified_jobs=[PageExtractor().extract(FAKE_JOB_PAGES[0])])
    runtime = HybridAgentRuntime(registry=AgentToolRegistry([]), policy=DeterministicAgentPolicy())
    assert runtime.deadline_state(state).terminal_status == "failed"


def test_cli_exports_do_not_present_unsaved_jobs_as_delivery():
    from web_task_agent.agent_cli import hybrid_state_payload

    state = DecisionAgentState(user=UserProfile(keyword="AI intern"),
                               verified_jobs=[PageExtractor().extract(FAKE_JOB_PAGES[0])],
                               terminal_status="failed", terminal_reason="storage_error")
    payload = hybrid_state_payload(state)
    assert payload["jobs"] == []
    assert len(payload["pending_jobs"]) == 1
