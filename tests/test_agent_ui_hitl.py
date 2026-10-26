from __future__ import annotations

import pytest

from web_task_agent.agent_approval import ApprovalDecision, ApprovalOutcome
from web_task_agent.models import UserProfile

from .test_agent_multitarget import make_workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["approve", "reject"])
async def test_ui_store_reconciles_terminal_checkpoint_after_context_write_fault(tmp_path, monkeypatch, outcome):
    import web_task_agent.agent_service as service
    from web_task_agent.artifact_store import FileArtifactStore
    from web_task_agent.run_store import FileRunStore
    from web_task_agent.storage import JobRepository
    from web_task_agent.streamlit_runner import UiRunRequest, resume_ui_request, run_ui_request

    stores = FileRunStore(tmp_path / "metadata"), FileArtifactStore(tmp_path / "artifacts")
    pending = await run_ui_request(
        UiRunRequest(keyword="AI intern", location="全国", target_count=1, engine="hybrid", hitl=True),
        output_root=tmp_path / "runs", owner_id="owner", environ={},
        run_store=stores[0], artifact_store=stores[1],
    )
    real_write = service.write_run_context
    faulted = False

    def context_write(root, context):
        nonlocal faulted
        if context.resolved_outcome and not faulted:
            faulted = True
            raise RuntimeError("context write interrupted after business save")
        return real_write(root, context)

    monkeypatch.setattr(service, "write_run_context", context_write)
    receipts = []
    save_once = JobRepository.save_jobs_once

    def record_save(repository, jobs, *, idempotency_key):
        receipts.append(idempotency_key)
        return save_once(repository, jobs, idempotency_key=idempotency_key)

    monkeypatch.setattr(JobRepository, "save_jobs_once", record_save)
    decision = ApprovalDecision(approval_id=pending.approval["approval_id"], outcome=outcome)
    arguments = dict(output_root=tmp_path / "runs", owner_id="owner", environ={},
                     run_store=stores[0], artifact_store=stores[1])
    with pytest.raises(RuntimeError, match="context write interrupted"):
        await resume_ui_request(pending.run_id, decision, **arguments)
    repository = JobRepository(tmp_path / "runs" / pending.run_id / "agent.db")
    assert len(repository.list_jobs()) == (1 if outcome == "approve" else 0)
    expected_pending = "running" if outcome == "approve" else "awaiting_approval"
    assert stores[0].get_run("owner", pending.run_id).status == expected_pending
    final = await resume_ui_request(pending.run_id, decision, **arguments)
    expected = "succeeded" if outcome == "approve" else "rejected"
    assert final.run_status == expected
    assert stores[0].get_run("owner", pending.run_id).status == expected
    repeated = await resume_ui_request(pending.run_id, decision, **arguments)
    assert repeated.run_status == expected
    assert len(receipts) == (1 if outcome == "approve" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["approve", "reject"])
async def test_service_persists_pause_and_resumes_new_runtime_without_navigation(tmp_path, outcome):
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunOptions, resume_agent_run, start_agent_run

    workflow, pages = make_workflow(tmp_path)
    user = UserProfile(
        keyword="AI intern",
        location="Remote",
        target_count=2,
        seed_urls=[page.url for page in pages],
        resume_text="PRIVATE RESUME",
    )
    pending = await start_agent_run(
        user,
        run_id="run-hitl",
        owner_id="owner-1",
        services=AgentServices(workflow=workflow, run_dir=tmp_path),
        options=AgentRunOptions(engine="hybrid", hitl=True, max_steps=30),
    )
    assert pending.status == "awaiting_approval"
    assert not workflow.repository.list_jobs()
    assert pending.approval["payload_hash"]
    assert "PRIVATE RESUME" not in str(pending.approval)

    class NoNavigation:
        async def open_url(self, url):
            raise AssertionError("resume cannot reopen or rediscover content")

    workflow.browser = NoNavigation()
    decision = ApprovalDecision(approval_id=pending.approval["approval_id"], outcome=ApprovalOutcome(outcome))
    resumed = await resume_agent_run(
        run_id="run-hitl",
        owner_id="owner-1",
        approval_decision=decision,
        services=AgentServices(workflow=workflow, run_dir=tmp_path),
    )
    assert resumed.status == ("succeeded" if outcome == "approve" else "rejected")
    assert len(workflow.repository.list_jobs()) == (2 if outcome == "approve" else 0)
    repeated = await resume_agent_run(
        run_id="run-hitl",
        owner_id="owner-1",
        approval_decision=decision,
        services=AgentServices(workflow=workflow, run_dir=tmp_path),
    )
    assert repeated.status == resumed.status
    assert len(workflow.repository.list_jobs()) == (2 if outcome == "approve" else 0)


@pytest.mark.asyncio
async def test_service_rejects_another_owner_before_resume(tmp_path):
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunOptions, resume_agent_run, start_agent_run

    workflow, pages = make_workflow(tmp_path, count=1)
    services = AgentServices(workflow=workflow, run_dir=tmp_path)
    pending = await start_agent_run(
        UserProfile(keyword="AI intern", location="Remote", target_count=1, seed_urls=[pages[0].url]),
        run_id="run-hitl",
        owner_id="owner-1",
        services=services,
        options=AgentRunOptions(engine="hybrid", hitl=True),
    )
    with pytest.raises(PermissionError):
        await resume_agent_run(
            run_id="run-hitl",
            owner_id="owner-2",
            services=services,
            approval_decision=ApprovalDecision(approval_id=pending.approval["approval_id"], outcome="approve"),
        )
    assert not workflow.repository.list_jobs()


@pytest.mark.asyncio
async def test_web_runner_exposes_and_resumes_approval_with_store_state(tmp_path):
    from web_task_agent.artifact_store import FileArtifactStore
    from web_task_agent.run_store import FileRunStore
    from web_task_agent.streamlit_runner import UiRunRequest, resume_ui_request, run_ui_request

    run_store = FileRunStore(tmp_path / "metadata")
    artifact_store = FileArtifactStore(tmp_path / "artifacts")
    pending = await run_ui_request(
        UiRunRequest(keyword="AI intern", location="全国", target_count=1, engine="hybrid", hitl=True),
        output_root=tmp_path / "runs",
        environ={},
        owner_id="owner-1",
        run_store=run_store,
        artifact_store=artifact_store,
    )
    assert pending.approval
    assert run_store.get_run("owner-1", pending.run_id).status == "awaiting_approval"
    final = await resume_ui_request(
        pending.run_id,
        ApprovalDecision(approval_id=pending.approval["approval_id"], outcome="approve"),
        output_root=tmp_path / "runs",
        owner_id="owner-1",
        environ={},
        run_store=run_store,
        artifact_store=artifact_store,
    )
    assert final.terminal_status == "completed"
    assert run_store.get_run("owner-1", pending.run_id).status == "succeeded"
    assert final.run_id == pending.run_id


@pytest.mark.asyncio
async def test_wrong_web_approval_does_not_poison_pending_run(tmp_path):
    from web_task_agent.artifact_store import FileArtifactStore
    from web_task_agent.run_store import FileRunStore
    from web_task_agent.streamlit_runner import UiRunRequest, resume_ui_request, run_ui_request

    run_store, artifact_store = FileRunStore(tmp_path / "meta"), FileArtifactStore(tmp_path / "artifacts")
    pending = await run_ui_request(
        UiRunRequest(keyword="AI intern", location="全国", target_count=1, engine="hybrid", hitl=True),
        output_root=tmp_path / "runs",
        environ={},
        owner_id="owner",
        run_store=run_store,
        artifact_store=artifact_store,
    )
    with pytest.raises(RuntimeError):
        await resume_ui_request(
            pending.run_id,
            ApprovalDecision(approval_id="wrong", outcome="approve"),
            output_root=tmp_path / "runs",
            owner_id="owner",
            environ={},
            run_store=run_store,
            artifact_store=artifact_store,
        )
    assert run_store.get_run("owner", pending.run_id).status == "awaiting_approval"


def test_approval_resumes_in_a_different_process(tmp_path):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    code = """
import asyncio,json,os,sys
from pathlib import Path
from web_task_agent.agent_approval import ApprovalDecision
from web_task_agent.agent_components import AgentServices
from web_task_agent.agent_run_context import read_run_context
from web_task_agent.agent_service import AgentRunOptions,start_agent_run,resume_agent_run
from web_task_agent.browser import FakeBrowserClient
from web_task_agent.extractor import PageExtractor
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import BrowserPage,UserProfile
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow
async def main():
    root=Path(sys.argv[1]); root.mkdir(parents=True,exist_ok=True)
    repo=JobRepository(root/'agent.db'); repo.initialize()
    pages=[BrowserPage(url=f'https://example.com/jobs/{i}',title=f'AI Intern {i}',
        content=(f'Title: AI Intern {i}\\nCompany: Example\\nLocation: Remote\\n'
                 'Requirements: Python AI\\nResponsibilities: Build AI systems')) for i in (1,2)]
    workflow=WebTaskWorkflow(browser=FakeBrowserClient(pages),extractor=PageExtractor(),
        matcher=JobMatcher(),verifier=JobVerifier(),repository=repo,reporter=MarkdownReporter(root/'reports'))
    services=AgentServices(workflow=workflow,run_dir=root)
    if sys.argv[2]=='start':
        result=await start_agent_run(UserProfile(keyword='AI intern',location='Remote',target_count=2,
            seed_urls=[p.url for p in pages]),run_id='run-process',owner_id='owner',services=services,
            options=AgentRunOptions(engine='hybrid',hitl=True,max_steps=30))
    else:
        context=read_run_context(root,run_id='run-process',owner_id='owner')
        result=await resume_agent_run(run_id='run-process',owner_id='owner',services=services,
            approval_decision=ApprovalDecision(approval_id=context.approval_id,outcome='approve'))
    print(json.dumps({'pid':os.getpid(),'status':result.status,'saved':len(repo.list_jobs())}))
asyncio.run(main())
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}

    def run(action):
        result = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path), action],
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        )
        return json.loads(result.stdout.strip())

    first = run("start")
    second = run("approve")
    repeated = run("approve")
    assert first["pid"] != second["pid"]
    assert first["status"] == "awaiting_approval" and first["saved"] == 0
    assert second["status"] == repeated["status"] == "succeeded"
    assert second["saved"] == repeated["saved"] == 2


@pytest.mark.asyncio
async def test_uploaded_aggregator_can_resume_after_original_input_is_removed(tmp_path):
    import json

    from tests.fixtures.link_receipts import VerifiedLinkStub
    from web_task_agent.models import BrowserPage
    from web_task_agent.streamlit_runner import UiRunRequest, resume_ui_request, run_ui_request

    source = tmp_path / "uploaded.json"
    source.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "official_url": "https://example.com/jobs/1",
                        "title": "AI Agent 实习生",
                        "company_name": "Example",
                        "location": "Remote",
                        "jd_text": "Responsibilities: Build AI agents with Python. " * 5,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    async def loader(url):
        return BrowserPage(
            url=url,
            title="AI Agent Intern",
            content=(
                "Title: AI Agent Intern\nCompany: Example\nLocation: Remote\n"
                "Requirements: Python, LangGraph\nResponsibilities: Build and evaluate AI Agent systems. " * 2
            ),
        )

    pending = await run_ui_request(
        UiRunRequest(
            keyword="AI intern",
            location="Remote",
            target_count=1,
            data_mode="aggregator",
            aggregator_path=str(source),
            engine="hybrid",
            hitl=True,
        ),
        output_root=tmp_path / "runs",
        page_loader=loader,
        link_verifier=VerifiedLinkStub(),
        owner_id="owner",
        environ={},
    )
    source.unlink()
    final = await resume_ui_request(
        pending.run_id,
        ApprovalDecision(approval_id=pending.approval["approval_id"], outcome="approve"),
        output_root=tmp_path / "runs",
        owner_id="owner",
        environ={},
    )
    assert final.run_status == "succeeded"
    assert final.resolution_log == pending.resolution_log
    assert final.data_mode == "aggregator"


@pytest.mark.asyncio
async def test_hitl_start_timeout_becomes_deadline_checkpoint_without_approval(tmp_path, monkeypatch):
    import asyncio

    import web_task_agent.agent_service as service
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_run_context import read_run_context

    workflow, pages = make_workflow(tmp_path, count=2)
    real_build = service.build_hybrid_runtime

    def slow_build(*args, **kwargs):
        runtime = real_build(*args, **kwargs)
        decide = runtime._decide_node

        async def slow_decide(state):
            await asyncio.sleep(1)
            return await decide(state)

        runtime._decide_node = slow_decide
        return runtime

    monkeypatch.setattr(service, "build_hybrid_runtime", slow_build)
    result = await service.start_agent_run(
        UserProfile(
            keyword="AI intern",
            location="Remote",
            target_count=2,
            seed_urls=[page.url for page in pages],
        ),
        run_id="run-timeout-hitl",
        owner_id="owner",
        services=AgentServices(workflow=workflow, run_dir=tmp_path),
        options=service.AgentRunOptions(engine="hybrid", hitl=True, deadline_seconds=0.02, max_steps=30),
    )
    assert result.status in {"partial", "failed"}
    assert result.approval is None
    assert result.agent_state.terminal_reason == "deadline_exceeded"
    assert not workflow.repository.list_jobs()
    context = read_run_context(tmp_path, run_id="run-timeout-hitl", owner_id="owner")
    assert context.status == result.status
    assert context.resolved_outcome == ""
    assert (tmp_path / "checkpoints.sqlite").is_file()
    loaded = await service.load_agent_run(
        run_id="run-timeout-hitl",
        owner_id="owner",
        services=AgentServices(workflow=workflow, run_dir=tmp_path),
    )
    assert loaded.status in {"partial", "failed"}
    assert loaded.approval is None
    assert loaded.agent_state.terminal_reason == "deadline_exceeded"


@pytest.mark.asyncio
async def test_hitl_timeout_after_pause_never_reports_awaiting_approval(tmp_path, monkeypatch):
    import asyncio

    import web_task_agent.agent_service as service
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_run_context import read_run_context

    workflow, pages = make_workflow(tmp_path, count=2)
    real_build = service.build_hybrid_runtime

    def late_build(*args, **kwargs):
        runtime = real_build(*args, **kwargs)
        start = runtime.start_hitl

        async def pause_then_miss_deadline(*start_args, **start_kwargs):
            await start(*start_args, **start_kwargs)
            await asyncio.sleep(1)

        runtime.start_hitl = pause_then_miss_deadline
        return runtime

    monkeypatch.setattr(service, "build_hybrid_runtime", late_build)
    result = await service.start_agent_run(
        UserProfile(
            keyword="AI intern",
            location="Remote",
            target_count=2,
            seed_urls=[page.url for page in pages],
        ),
        run_id="run-late-hitl",
        owner_id="owner",
        services=AgentServices(workflow=workflow, run_dir=tmp_path),
        options=service.AgentRunOptions(engine="hybrid", hitl=True, deadline_seconds=0.05, max_steps=30),
    )
    assert result.status in {"partial", "failed"}
    assert result.approval is None
    assert result.agent_state.pending_approval is None
    assert not workflow.repository.list_jobs()
    context = read_run_context(tmp_path, run_id="run-late-hitl", owner_id="owner")
    assert context.status == result.status
    assert context.approval_id == ""


@pytest.mark.asyncio
async def test_hitl_rejects_memory_repository_before_context_or_checkpoint(tmp_path):
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_run_context import read_run_context
    from web_task_agent.agent_service import AgentRunOptions, start_agent_run
    from web_task_agent.storage import JobRepository

    workflow, _pages = make_workflow(tmp_path, count=1)
    workflow.repository = JobRepository(":memory:")
    workflow.repository.initialize()
    with pytest.raises(ValueError, match=":memory:"):
        await start_agent_run(
            UserProfile(keyword="AI intern", location="Remote", target_count=1),
            run_id="run-memory-hitl",
            owner_id="owner",
            services=AgentServices(workflow=workflow, run_dir=tmp_path),
            options=AgentRunOptions(engine="hybrid", hitl=True),
        )
    assert not (tmp_path / "run-context.json").exists()
    assert not (tmp_path / "checkpoints.sqlite").exists()
    with pytest.raises(FileNotFoundError):
        read_run_context(tmp_path, run_id="run-memory-hitl", owner_id="owner")
