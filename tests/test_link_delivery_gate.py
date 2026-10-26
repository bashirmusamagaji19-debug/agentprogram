from datetime import UTC, datetime, timedelta

import pytest

from tests.fixtures.job_pages import FAKE_JOB_PAGES
from web_task_agent.agent_models import DecisionAgentState
from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.agent_tools import SaveResultsTool, VerifyJobTool
from web_task_agent.browser import FakeBrowserClient
from web_task_agent.extractor import PageExtractor
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import UserProfile
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow


def receipt(job, run_id, status="verified"):
    return dict(status=status, run_id=run_id, requested_url=job.url, final_url=job.url,
                checked_at=datetime.now(UTC).isoformat(), method="browser",
                reason="matched_live_detail", evidence_sha256="a" * 64,
                title_matched=True, jd_matched=True, active=True)


class Checker:
    def __init__(self, status):
        self.status = status
        self.calls = []

    async def verify(self, job, *, run_id):
        self.calls.append(job.url)
        return receipt(job, run_id, self.status)


def state():
    job = PageExtractor().extract(FAKE_JOB_PAGES[0])
    return DecisionAgentState(user=UserProfile(keyword="AI intern"), execution_id="run-link-test",
                              extracted_jobs=[job], current_url=job.url,
                              candidate_urls=[job.url],
                              require_verified_links=True, visual_available=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "rejected"])
async def test_unconfirmed_link_does_not_count_toward_target(status):
    s = state()
    checker = Checker(status)
    observation = await VerifyJobTool(JobVerifier(), link_verifier=checker).execute(s, {})
    assert not observation.success
    assert observation.error_category == "link_" + status
    assert not s.verified_jobs
    assert len(s.pending_jobs) == (1 if status == "pending" else 0)
    assert s.current_url in s.processed_urls
    assert observation.recoverable is False
    s.last_observation = observation
    # A failed link cannot be repaired by re-extracting the same page visually.
    assert DeterministicAgentPolicy().decide(s).action.value == "finish"


@pytest.mark.asyncio
async def test_verified_link_attaches_receipt_before_acceptance():
    s = state()
    observation = await VerifyJobTool(JobVerifier(), link_verifier=Checker("verified")).execute(s, {})
    assert observation.success
    assert s.verified_jobs[0].link_verification["run_id"] == s.execution_id


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", ["absent", "previous_run", "expired"])
async def test_save_boundary_rejects_missing_or_stale_proof(tmp_path, proof):
    s = state()
    job = s.extracted_jobs[0]
    if proof != "absent":
        check = receipt(job, "old-run" if proof == "previous_run" else s.execution_id)
        if proof == "expired":
            check["checked_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        job = job.model_copy(update={"link_verification": check})
    s.verified_jobs = [job]
    repo = JobRepository(tmp_path / "jobs.db")
    repo.initialize()
    observation = await SaveResultsTool(repo).execute(s, {})
    assert not observation.success
    assert observation.error_category == "link_verification_required"
    assert not s.saved
    assert not repo.list_jobs()
    assert not s.verified_jobs
    assert len(s.pending_jobs) == 1


@pytest.mark.asyncio
async def test_fixed_workflow_has_same_delivery_gate(tmp_path):
    repo = JobRepository(tmp_path / "jobs.db")
    repo.initialize()
    checker = Checker("pending")
    workflow = WebTaskWorkflow(browser=FakeBrowserClient(FAKE_JOB_PAGES), extractor=PageExtractor(),
        matcher=JobMatcher(), verifier=JobVerifier(), repository=repo,
        reporter=MarkdownReporter(tmp_path / "reports"), require_verified_links=True,
        link_verifier=checker)
    result = await workflow.run_with_langgraph(UserProfile(keyword="AI intern", target_count=1), run_id="run-fixed")
    assert checker.calls
    assert result.jobs == []
    assert result.metrics.valid_jobs == 0
    assert result.pending_jobs
    assert not repo.list_jobs()


@pytest.mark.asyncio
async def test_plain_workflow_cannot_bypass_gate(tmp_path):
    repo = JobRepository(tmp_path / "jobs.db")
    repo.initialize()
    workflow = WebTaskWorkflow(browser=FakeBrowserClient(FAKE_JOB_PAGES), extractor=PageExtractor(),
        matcher=JobMatcher(), verifier=JobVerifier(), repository=repo,
        reporter=MarkdownReporter(tmp_path / "reports"), require_verified_links=True,
        link_verifier=Checker("verified"))
    result = await workflow.run(UserProfile(keyword="AI intern", target_count=1), run_id="run-plain")
    assert result.jobs
    assert all(job.link_verification["run_id"] == "run-plain" for job in result.jobs)
    assert repo.list_jobs()


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh_status", [200, 404])
async def test_real_ui_request_uses_independent_link_evidence(tmp_path, fresh_status):
    from web_task_agent.browser import PageHttpError
    from web_task_agent.link_verification import JobLinkVerifier
    from web_task_agent.models import BrowserPage
    from web_task_agent.streamlit_runner import UiRunRequest, run_ui_request

    url = "https://example.com/jobs/12345"
    content = ("Title: AI Agent Intern\nCompany: Example\nLocation: Remote\n"
               "Requirements: Python, LangGraph\n"
               "Responsibilities: Build reliable Python services for AI Agent evaluation and deployment.\n")
    checks = []

    async def source_loader(requested):
        # This proves extraction only; a separate fresh visit must succeed.
        return BrowserPage(url=requested, title="AI Agent Intern", content=content,
                           metadata={"content_origin": "official_api"})

    async def link_loader(requested):
        checks.append(requested)
        if fresh_status == 404:
            raise PageHttpError("gone", 404)
        return BrowserPage(url=requested, title="AI Agent Intern", content=content,
            metadata={"requested_url": requested, "final_url": requested, "final_url_observed": True,
                      "status_code": 200,
                      "apply_controls": [{"tag": "button", "text": "Apply now",
                                          "visible": True, "enabled": True}]})

    result = await run_ui_request(
        UiRunRequest(data_mode="seed_urls", seed_urls=[url], keyword="AI intern", location="Remote",
                     target_count=1, engine="hybrid"),
        output_root=tmp_path, environ={}, page_loader=source_loader,
        link_verifier=JobLinkVerifier(http_loader=link_loader,
                                    browser_loader=link_loader if fresh_status == 200 else None,
                                    enable_browser_fallback=fresh_status == 200,
                                    evidence_dir=tmp_path / "probe-evidence"))
    repo = JobRepository(tmp_path / result.run_id / "agent.db")
    assert checks == [url] * (2 if fresh_status == 200 else 1)
    if fresh_status == 404:
        assert not result.jobs and not result.saved and not repo.list_jobs()
        assert result.progress["delivered"] == 0
        assert any("gone" in row["error"] for row in result.diagnostics)
    else:
        assert result.saved and len(repo.list_jobs()) == 1
        assert result.jobs[0].link_verification["run_id"] == result.run_id
        assert result.progress["delivered"] == 1


@pytest.mark.asyncio
async def test_retained_link_evidence_is_exportable(tmp_path):
    from web_task_agent.streamlit_runner import _bundle_link_checks

    run_dir = tmp_path / "run-evidence"
    evidence = run_dir / "link-checks"
    evidence.mkdir(parents=True)
    (evidence / ("a" * 64 + ".json")).write_text('{"content":"public job body"}', encoding="utf-8")
    path = _bundle_link_checks(run_dir)
    import zipfile

    with zipfile.ZipFile(path) as bundle:
        assert bundle.namelist() == ["link-checks/" + "a" * 64 + ".json"]
        assert b"public job body" in bundle.read(bundle.namelist()[0])


@pytest.mark.asyncio
async def test_approval_resume_cannot_save_an_expired_link_receipt(tmp_path, monkeypatch):
    import web_task_agent.link_verification as links
    from web_task_agent.agent_approval import ApprovalDecision
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunOptions, resume_agent_run, start_agent_run

    repo = JobRepository(tmp_path / "jobs.db")
    repo.initialize()
    workflow = WebTaskWorkflow(browser=FakeBrowserClient(FAKE_JOB_PAGES), extractor=PageExtractor(),
        matcher=JobMatcher(), verifier=JobVerifier(), repository=repo,
        reporter=MarkdownReporter(tmp_path / "reports"), require_verified_links=True,
        link_verifier=Checker("verified"))
    services = AgentServices(workflow=workflow, run_dir=tmp_path / "run-approval")
    pending = await start_agent_run(
        UserProfile(keyword="AI intern", target_count=1, seed_urls=[FAKE_JOB_PAGES[0].url]),
        run_id="run-approval", owner_id="tester", services=services,
        options=AgentRunOptions(engine="hybrid", hitl=True))
    assert pending.status == "awaiting_approval"
    assert not repo.list_jobs()
    original_datetime = links.datetime

    class Later(original_datetime):
        @classmethod
        def now(cls, tz=None):
            return original_datetime.now(tz) + timedelta(hours=1)

    monkeypatch.setattr(links, "datetime", Later)
    # Resume has no source navigation, but must retain the original live gate.
    workflow.require_verified_links = False
    final = await resume_agent_run(run_id="run-approval", owner_id="tester", services=services,
        approval_decision=ApprovalDecision(approval_id=pending.approval["approval_id"], outcome="approve"))
    assert final.status == "failed"
    assert final.view.jobs == [] and final.view.pending_jobs
    assert not final.agent_state.saved
    assert final.agent_state.terminal_reason == "link_verification_required"
    assert not repo.list_jobs()
