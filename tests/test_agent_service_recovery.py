from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from web_task_agent.agent_approval import ApprovalDecision, HitlRuntimeError
from web_task_agent.agent_run_context import read_run_context, write_run_context
from web_task_agent.models import UserProfile

from .test_agent_multitarget import make_workflow


class SimulatedCrash(RuntimeError):
    pass


@pytest.mark.asyncio
async def test_source_metadata_is_finalized_before_the_single_report_write(tmp_path):
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunOptions, start_agent_run

    workflow, pages = make_workflow(tmp_path, count=1)
    finalize_calls = []

    def finalize_metadata(run_dir):
        finalize_calls.append(run_dir)
        return {
            "source_manifest": "source-archive/manifest.json",
            "source_mode": "replay",
            "source_captured_at": "2026-10-23T08:30:00Z",
        }

    workflow.browser.finalize_metadata = finalize_metadata
    reports = []
    write_report = workflow.reporter.write_report

    def capture_report(**kwargs):
        reports.append(kwargs)
        return write_report(**kwargs)

    workflow.reporter.write_report = capture_report
    result = await start_agent_run(
        UserProfile(
            keyword="AI intern",
            location="Remote",
            target_count=1,
            seed_urls=[pages[0].url],
        ),
        run_id="run-source-metadata",
        owner_id="owner",
        services=AgentServices(workflow=workflow, run_dir=tmp_path),
        options=AgentRunOptions(engine="hybrid"),
    )

    assert finalize_calls == [tmp_path]
    assert len(reports) == 1
    assert reports[0]["run_details"]["source_manifest"] == "source-archive/manifest.json"
    assert reports[0]["run_details"]["source_mode"] == "replay"
    assert reports[0]["run_details"]["source_captured_at"] == "2026-10-23T08:30:00Z"
    assert result.view.metadata["source_manifest"] == "source-archive/manifest.json"


async def _start_pending(tmp_path, *, run_id="run-recovery", owner_id="owner"):
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunOptions, start_agent_run

    workflow, pages = make_workflow(tmp_path, count=2)
    pending = await start_agent_run(
        UserProfile(
            keyword="AI intern",
            location="Remote",
            target_count=2,
            seed_urls=[page.url for page in pages],
        ),
        run_id=run_id,
        owner_id=owner_id,
        services=AgentServices(workflow=workflow, run_dir=tmp_path),
        options=AgentRunOptions(engine="hybrid", hitl=True, max_steps=30),
    )
    return workflow, pages, pending


async def _crash_after_approval_gate(tmp_path, monkeypatch, pending):
    import web_task_agent.agent_service as service

    real_build = service.build_hybrid_runtime
    crashed = [False]

    def build_with_execution_crash(*args, **kwargs):
        runtime = real_build(*args, **kwargs)
        execute = runtime._execute_tool_node

        async def crash_before_execute(state):
            if (
                not crashed[0]
                and state.last_decision is not None
                and state.last_decision.action.value == "save_results"
            ):
                crashed[0] = True
                raise SimulatedCrash("crash after approval gate checkpoint")
            return await execute(state)

        runtime._execute_tool_node = crash_before_execute
        return runtime

    monkeypatch.setattr(service, "build_hybrid_runtime", build_with_execution_crash)
    with pytest.raises(SimulatedCrash):
        await service.resume_agent_run(
            run_id="run-recovery",
            owner_id="owner",
            approval_decision=ApprovalDecision(approval_id=pending.approval["approval_id"], outcome="approve"),
            services=service.AgentServices(workflow=make_workflow(tmp_path, count=2)[0], run_dir=tmp_path),
        )
    monkeypatch.setattr(service, "build_hybrid_runtime", real_build)


@pytest.mark.asyncio
async def test_terminal_checkpoint_recovery_finalizes_context_without_resaving(tmp_path, monkeypatch):
    import web_task_agent.agent_service as service

    workflow, _pages, pending = await _start_pending(tmp_path)
    receipts = []
    save_once = workflow.repository.save_jobs_once

    def inspect_receipt(jobs, *, idempotency_key):
        receipt = save_once(jobs, idempotency_key=idempotency_key)
        receipts.append(receipt)
        return receipt

    workflow.repository.save_jobs_once = inspect_receipt
    real_write = service.write_run_context
    crashed = [False]

    def crash_before_resolved_context(root, context):
        if context.resolved_outcome and not crashed[0]:
            crashed[0] = True
            raise SimulatedCrash("crash before resolved_outcome write")
        return real_write(root, context)

    monkeypatch.setattr(service, "write_run_context", crash_before_resolved_context)
    decision = ApprovalDecision(approval_id=pending.approval["approval_id"], outcome="approve")
    with pytest.raises(SimulatedCrash):
        await service.resume_agent_run(
            run_id="run-recovery",
            owner_id="owner",
            approval_decision=decision,
            services=service.AgentServices(workflow=workflow, run_dir=tmp_path),
        )

    stored = read_run_context(tmp_path, run_id="run-recovery", owner_id="owner")
    assert stored.resolved_outcome == ""
    assert len(workflow.repository.list_jobs()) == 2
    assert len(receipts) == 1

    with pytest.raises(HitlRuntimeError, match="different outcome"):
        await service.resume_agent_run(
            run_id="run-recovery",
            owner_id="owner",
            approval_decision=ApprovalDecision(approval_id=pending.approval["approval_id"], outcome="reject"),
            services=service.AgentServices(workflow=workflow, run_dir=tmp_path),
        )
    assert len(receipts) == 1

    repeated = await service.resume_agent_run(
        run_id="run-recovery",
        owner_id="owner",
        approval_decision=decision,
        services=service.AgentServices(workflow=workflow, run_dir=tmp_path),
    )
    assert repeated.status == "succeeded"
    assert len(receipts) == 1
    finalized = read_run_context(tmp_path, run_id="run-recovery", owner_id="owner")
    assert finalized.resolved_outcome == "approve"
    assert finalized.status == "completed"


@pytest.mark.asyncio
async def test_gate_crash_resumes_queued_execution_without_second_approval(tmp_path, monkeypatch):
    import web_task_agent.agent_service as service

    workflow, _pages, pending = await _start_pending(tmp_path)
    decision = ApprovalDecision(approval_id=pending.approval["approval_id"], outcome="approve")
    await _crash_after_approval_gate(tmp_path, monkeypatch, pending)

    stored = read_run_context(tmp_path, run_id="run-recovery", owner_id="owner")
    assert stored.resolved_outcome == ""
    assert not workflow.repository.list_jobs()
    async with service.open_sqlite_checkpointer(stored.checkpoint_path) as saver:
        runtime = service.build_hybrid_runtime(workflow, checkpointer=saver)
        checkpoint = await runtime.load_hitl_checkpoint(stored.thread_id)
    assert checkpoint.pending_execution is True
    assert checkpoint.has_interrupt is False

    resumed = await service.resume_agent_run(
        run_id="run-recovery",
        owner_id="owner",
        approval_decision=decision,
        services=service.AgentServices(workflow=workflow, run_dir=tmp_path),
    )
    assert resumed.status == "succeeded"
    assert len(workflow.repository.list_jobs()) == 2
    resolved_events = [event for event in resumed.agent_state.approval_audit if event.event == "resolved"]
    assert len(resolved_events) == 1


@pytest.mark.asyncio
async def test_gate_crash_rejects_changed_outcome_or_payload(tmp_path, monkeypatch):
    import web_task_agent.agent_service as service

    workflow, _pages, pending = await _start_pending(tmp_path)
    await _crash_after_approval_gate(tmp_path, monkeypatch, pending)
    with pytest.raises(HitlRuntimeError, match="different outcome"):
        await service.resume_agent_run(
            run_id="run-recovery",
            owner_id="owner",
            approval_decision=ApprovalDecision(approval_id=pending.approval["approval_id"], outcome="reject"),
            services=service.AgentServices(workflow=workflow, run_dir=tmp_path),
        )

    with pytest.raises(HitlRuntimeError, match="approval_id"):
        await service.resume_agent_run(
            run_id="run-recovery",
            owner_id="owner",
            approval_decision=ApprovalDecision(approval_id="approval-wrong", outcome="approve"),
            services=service.AgentServices(workflow=workflow, run_dir=tmp_path),
        )

    context = read_run_context(tmp_path, run_id="run-recovery", owner_id="owner")
    context.payload_hash = hashlib.sha256(b"different batch").hexdigest()
    write_run_context(tmp_path, context)
    with pytest.raises(HitlRuntimeError, match="payload"):
        await service.resume_agent_run(
            run_id="run-recovery",
            owner_id="owner",
            approval_decision=ApprovalDecision(approval_id=pending.approval["approval_id"], outcome="approve"),
            services=service.AgentServices(workflow=workflow, run_dir=tmp_path),
        )
    assert not workflow.repository.list_jobs()


@pytest.mark.asyncio
async def test_expired_owner_context_is_rejected_before_checkpoint_execution(tmp_path):
    import web_task_agent.agent_service as service

    workflow, _pages, pending = await _start_pending(tmp_path)
    context = read_run_context(tmp_path, run_id="run-recovery", owner_id="owner")
    context.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    write_run_context(tmp_path, context)
    with pytest.raises(ValueError, match="expired"):
        await service.resume_agent_run(
            run_id="run-recovery",
            owner_id="owner",
            approval_decision=ApprovalDecision(approval_id=pending.approval["approval_id"], outcome="approve"),
            services=service.AgentServices(workflow=workflow, run_dir=tmp_path),
        )
    assert not workflow.repository.list_jobs()
