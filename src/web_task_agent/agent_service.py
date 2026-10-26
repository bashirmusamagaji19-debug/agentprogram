"""Application orchestration shared by browser UI, CLI and acceptance scripts."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from web_task_agent.agent_approval import (
    ApprovalDecision,
    HitlRunStatus,
    HitlRuntimeError,
    approval_batch_hash,
)
from web_task_agent.agent_checkpoint import open_sqlite_checkpointer
from web_task_agent.agent_components import AgentServices, build_hybrid_runtime
from web_task_agent.agent_models import AgentAction, AgentBudget, DecisionAgentState
from web_task_agent.agent_run_context import (
    RunContext,
    lock_run,
    read_run_context,
    require_run_identity,
    write_run_context,
)
from web_task_agent.models import RunMetrics, UserProfile, WorkflowState
from web_task_agent.source_metrics import build_source_metrics


class AgentRunRequest(BaseModel):
    keyword: str = "AI Agent 实习"
    location: str = "全国"
    target_count: int = Field(default=10, ge=1, le=50)
    skills: list[str] = Field(default_factory=list)
    resume_text: str = ""
    data_mode: Literal["demo", "aggregator", "seed_urls", "official"] = "demo"
    aggregator_path: str | None = None
    aggregator_url: str | None = None
    official_specs: list[str] | None = None
    seed_urls: list[str] = Field(default_factory=list)
    llm_extractor_provider: Literal["deepseek", "qwen"] | None = None
    llm_match_provider: Literal["deepseek", "qwen"] | None = None
    engine: Literal["workflow", "hybrid"] = "workflow"
    planner_strategy: Literal["policy", "always", "adaptive"] = "policy"
    planner_provider: Literal["deepseek", "qwen"] | None = None
    planner_model: str | None = None
    hitl: bool = False
    max_steps: int | None = Field(default=None, ge=1, le=400)
    deadline_seconds: float = Field(default=90, gt=0, le=600)
    max_planner_calls: int = Field(default=4, ge=0, le=100)
    planner_timeout_seconds: float = Field(default=20, gt=0, le=60)

    def options(self, candidate_count: int | None = None) -> AgentRunOptions:
        max_steps = self.max_steps
        if max_steps is None:
            max_steps = (min(100, 4 * min(max(0, candidate_count), 10) + 4)
                         if candidate_count is not None else min(400, max(12, 6 * self.target_count + 8)))
        return AgentRunOptions(
            engine=self.engine,
            planner_strategy=self.planner_strategy,
            planner_provider=self.planner_provider,
            planner_model=self.planner_model,
            hitl=self.hitl,
            max_steps=max_steps,
            deadline_seconds=self.deadline_seconds,
            max_planner_calls=self.max_planner_calls,
            planner_timeout_seconds=self.planner_timeout_seconds,
        )


class AgentRunOptions(BaseModel):
    engine: Literal["workflow", "hybrid"] = "workflow"
    planner_strategy: Literal["policy", "always", "adaptive"] = "policy"
    planner_provider: Literal["deepseek", "qwen"] | None = None
    planner_model: str | None = None
    hitl: bool = False
    max_steps: int = Field(default=30, ge=1, le=400)
    deadline_seconds: float = Field(default=180, gt=0, le=600)
    max_planner_calls: int = Field(default=4, ge=0, le=100)
    planner_timeout_seconds: float = Field(default=20, gt=0, le=60)

    @model_validator(mode="after")
    def validate_options(self):
        if self.hitl and self.engine != "hybrid":
            raise ValueError("HITL requires hybrid engine")
        if self.planner_strategy != "policy" and not self.planner_provider:
            raise ValueError("LLM strategy requires a provider")
        if self.engine == "workflow" and self.planner_strategy != "policy":
            raise ValueError("Planner strategies require hybrid engine")
        return self


@dataclass
class AgentServiceResult:
    view: WorkflowState
    status: str
    agent_state: DecisionAgentState | None = None
    approval: dict[str, Any] | None = None


async def start_agent_run(
    request: UserProfile | AgentRunRequest,
    *,
    run_id: str,
    owner_id: str,
    services: AgentServices,
    options: AgentRunOptions | None = None,
) -> AgentServiceResult:
    require_run_identity(run_id)
    if not owner_id.strip():
        raise ValueError("run_id and owner_id must not be blank")
    user = (
        request
        if isinstance(request, UserProfile)
        else UserProfile(
            keyword=request.keyword,
            location=request.location,
            target_count=request.target_count,
            skills=request.skills,
            resume_text=request.resume_text,
            seed_urls=request.seed_urls,
        )
    )
    options = options or (request.options() if isinstance(request, AgentRunRequest) else AgentRunOptions())
    workflow = services.workflow
    if isinstance(request, AgentRunRequest) and request.data_mode != "demo":
        workflow.require_verified_links = True
    source_deadline = getattr(workflow.browser, "request_deadline", None)
    deadline_seconds = options.deadline_seconds
    if source_deadline is not None:
        deadline_seconds = min(deadline_seconds, max(0, source_deadline - monotonic()))
    if options.hitl and str(workflow.repository.db_path) == ":memory:":
        raise ValueError("HITL persistent context requires a file-backed repository; ':memory:' is unsupported")
    if options.engine == "workflow":
        view = await asyncio.wait_for(
            workflow.run_with_langgraph(user=user, run_id=run_id), timeout=deadline_seconds
        )
        return AgentServiceResult(view=view, status=("succeeded" if len(view.jobs) >= user.target_count
                                                     else "partial" if view.jobs else "failed"))
    planner = services.planner
    if planner is None and options.planner_strategy != "policy":
        from web_task_agent.agent_planner import build_configured_agent_planner

        planner = build_configured_agent_planner(
            provider=options.planner_provider,
            model=options.planner_model,
            budget=services.model_budget,
            timeout_seconds=int(options.planner_timeout_seconds),
        )
    started_at = datetime.now(UTC)
    source_reference = getattr(workflow.browser, "reference_metadata", None)
    source_metadata = source_reference(services.run_dir) if callable(source_reference) else {}
    state = DecisionAgentState(
        user=user,
        execution_id=run_id,
        budget=AgentBudget(max_steps=options.max_steps),
        candidate_urls=list(dict.fromkeys(user.seed_urls))[:100],
        visual_available=workflow.visual_extractor is not None,
        enforce_search_conditions=True,
        require_verified_links=getattr(workflow, "require_verified_links", False),
        candidate_summaries=dict(getattr(workflow.browser, "candidate_summaries", {})),
        source_metadata=source_metadata,
        unmetered_model_roles=[role for role, configured in (
            ("reader", getattr(workflow.extractor, "llm_field_extractor", None)),
            ("matcher", getattr(workflow.matcher, "llm_matcher", None)),
        ) if configured is not None],
    )
    runtime_options = {
        "planner_strategy": options.planner_strategy,
        "max_planner_calls": options.max_planner_calls,
        "planner_timeout_seconds": options.planner_timeout_seconds,
        "deadline_seconds": max(.001, deadline_seconds),
    }
    if options.hitl:
        with lock_run(services.run_dir):
            if (services.run_dir / "run-context.json").exists():
                raise ValueError("run already exists; resume the stored approval")
            context = RunContext(
                run_id=run_id,
                owner_id=owner_id,
                thread_id=services.thread_id or hashlib.sha256(f"{owner_id}\0{run_id}".encode()).hexdigest(),
                repository_path=str(workflow.repository.db_path.resolve()),
                checkpoint_path=str((services.checkpoint_path or services.run_dir / "checkpoints.sqlite").resolve()),
                user=user,
                options=options.model_dump(),
                started_at=started_at,
                source_manifest=source_metadata.get("source_manifest"),
                source_mode=source_metadata.get("source_mode", ""),
                require_verified_links=state.require_verified_links,
            )
            write_run_context(services.run_dir, context)
            async with open_sqlite_checkpointer(context.checkpoint_path) as saver:
                runtime = build_hybrid_runtime(workflow, planner=planner, checkpointer=saver, **runtime_options)
                try:
                    result = await asyncio.wait_for(
                        runtime.start_hitl(state, thread_id=context.thread_id), timeout=deadline_seconds
                    )
                except TimeoutError:
                    result = await runtime.expire_hitl_start(state, thread_id=context.thread_id)
            awaiting_approval = result.status is HitlRunStatus.AWAITING_APPROVAL
            context.candidate_ids = list(dict.fromkeys(
                item.candidate_id for item in getattr(workflow.browser, "candidates", {}).values()
            ))
            context.status = "awaiting_approval" if awaiting_approval else result.state.terminal_status
            if awaiting_approval and result.approval is not None:
                context.approval_id = result.approval.approval_id
                context.payload_hash = result.approval.payload_hash
            write_run_context(services.run_dir, context)
            return _result_from_state(
                result.state, services, run_id, started_at, status="awaiting_approval" if awaiting_approval else None
            )
    runtime = build_hybrid_runtime(workflow, planner=planner, **runtime_options)
    try:
        state = await asyncio.wait_for(runtime.run(state), timeout=deadline_seconds)
    except TimeoutError:
        state = runtime.deadline_state(state)
    return _result_from_state(state, services, run_id, started_at)


def _result_from_state(state, services, run_id, started_at, *, status=None):
    workflow = services.workflow
    view = project_hybrid_state(state, run_id=run_id, started_at=started_at)
    view.metadata.update(state.source_metadata)
    view.metadata["unmetered_model_roles"] = state.unmetered_model_roles
    view.metadata["model_usage_complete"] = not state.unmetered_model_roles and all(
        record.get("usage_complete", False) for record in state.model_usage
    )
    finalize_metadata = getattr(workflow.browser, "finalize_metadata", None)
    if callable(finalize_metadata):
        source_metadata = finalize_metadata(services.run_dir)
        if source_metadata:
            view.metadata.update(source_metadata)
    archive = getattr(workflow.browser, "archive", None)
    if archive is not None:
        delivered_urls = list(dict.fromkeys(job.url for job in view.jobs)) if state.saved else []
        verification_urls = [str(observation.payload["job"]["url"])
                             for observation in state.observation_history
                             if observation.tool_name is AgentAction.VERIFY_JOB
                             and isinstance(observation.payload.get("job"), dict)
                             and observation.payload["job"].get("url")]
        source_metrics, source_evidence = build_source_metrics(
            archive,
            source_diagnostics=list(getattr(workflow.browser, "source_diagnostics", [])),
            delivered_urls=delivered_urls,
            verified_urls=[job.url for job in state.verified_jobs],
            verification_urls=verification_urls,
        )
        view.metadata["source_metrics"] = source_metrics
        view.metadata["source_evidence"] = source_evidence
    view.report_path = str(
        workflow.reporter.write_report(
            user=state.user,
            jobs=view.jobs,
            matches=view.matches,
            metrics=view.metrics,
            execution_trace=view.metadata["execution_trace"],
            orchestration_mode="hybrid",
            run_details={**view.metadata, "terminal_status": status or state.terminal_status},
        )
    )
    return AgentServiceResult(
        view=view,
        agent_state=state,
        status=status
        or {"completed": "succeeded", "partial": "partial", "failed": "failed", "rejected": "rejected"}.get(
            state.terminal_status, "failed"
        ),
        approval=state.pending_approval.public_payload() if state.pending_approval is not None else None,
    )


async def load_agent_run(*, run_id: str, owner_id: str, services: AgentServices) -> AgentServiceResult:
    context = read_run_context(services.run_dir, run_id=run_id, owner_id=owner_id)
    from pathlib import Path

    if not Path(context.checkpoint_path).is_file():
        raise ValueError("run checkpoint is missing")
    async with open_sqlite_checkpointer(context.checkpoint_path) as saver:
        runtime = build_hybrid_runtime(services.workflow, checkpointer=saver)
        checkpoint = await runtime.load_hitl_checkpoint(context.thread_id)
        state = checkpoint.state
    pending_approval = (
        checkpoint.approval is not None and checkpoint.approval.status.value == "pending" and checkpoint.has_interrupt
    )
    return _result_from_state(
        state,
        services,
        run_id,
        context.started_at,
        status=("awaiting_approval" if pending_approval else "running" if state.terminal_status == "running" else None),
    )


async def resume_agent_run(
    *, run_id: str, owner_id: str, approval_decision: ApprovalDecision, services: AgentServices
) -> AgentServiceResult:
    with lock_run(services.run_dir):
        context = read_run_context(services.run_dir, run_id=run_id, owner_id=owner_id)
        # A fresh fake browser is used for approval recovery. Restore the
        # original delivery policy instead of inferring it from that browser.
        services.workflow.require_verified_links = (
            context.require_verified_links if context.require_verified_links is not None
            else context.source_mode not in {"demo", "replay"})
        if approval_decision.approval_id != context.approval_id:
            raise HitlRuntimeError("approval_id does not match the pending request")
        if str(services.workflow.repository.db_path.resolve()) != context.repository_path:
            raise ValueError("run repository path does not match the saved context")
        if context.resolved_outcome:
            if context.resolved_outcome != approval_decision.outcome.value:
                raise HitlRuntimeError("approval was already resolved with a different outcome")
            return await load_agent_run(run_id=run_id, owner_id=owner_id, services=services)
        async with open_sqlite_checkpointer(context.checkpoint_path) as saver:
            runtime = build_hybrid_runtime(services.workflow, checkpointer=saver)
            checkpoint = await runtime.load_hitl_checkpoint(context.thread_id)
            state = checkpoint.state
            request = checkpoint.approval
            if request is None or request.approval_id != context.approval_id:
                raise HitlRuntimeError("approval_id does not match the saved approval receipt")
            if context.payload_hash != approval_batch_hash(state.verified_jobs, state.matches):
                raise HitlRuntimeError("approved payload differs from the saved context")
            result = await runtime.recover_hitl(thread_id=context.thread_id, decision=approval_decision)
        context.resolved_outcome = approval_decision.outcome.value
        context.status = result.state.terminal_status
        write_run_context(services.run_dir, context)
        return _result_from_state(result.state, services, run_id, context.started_at)


def project_hybrid_state(state: DecisionAgentState, *, run_id: str, started_at: datetime) -> WorkflowState:
    unsaved = not state.saved and state.terminal_status not in {"running", "awaiting_approval"}
    jobs = [] if unsaved else state.verified_jobs
    pending_jobs = list(state.pending_jobs)
    if unsaved:
        pending_jobs.extend(job.model_copy(update={"link_verification": {
            **job.link_verification, "status": "pending",
            "reason": f"link_checked_but_results_not_saved: {state.terminal_reason}; run again"}})
                            for job in state.verified_jobs)
    failures = []
    filtered = []
    trace = []
    legacy_trace = []
    remaining = state.budget.max_steps
    for index, decision in enumerate(state.decision_history):
        observation = state.observation_history[index] if index < len(state.observation_history) else None
        telemetry = state.decision_telemetry[index] if index < len(state.decision_telemetry) else {}
        remaining = telemetry.get("remaining_budget", remaining)
        if observation is not None and decision.action is not AgentAction.FINISH:
            remaining = max(0, remaining - 1)
        row = {
            "action": decision.action.value,
            "source": decision.source.value,
            "target": decision.target,
            "reason": decision.reason,
            "observation": observation.model_dump(mode="json") if observation else None,
            "remaining_budget": remaining,
            "planner_usage": telemetry.get("planner_usage", []),
        }
        trace.append(row)
        if observation is None:
            legacy_trace.append({"node": decision.action.value, "summary": "Waiting for approval."})
            continue
        legacy_trace.append({"node": decision.action.value, "summary": observation.summary})
        if not observation.success:
            url = decision.target or str(observation.payload.get("job", {}).get("url", ""))
            if observation.tool_name is AgentAction.VERIFY_JOB:
                filtered.append(
                    {
                        "url": url,
                        "reasons": observation.payload.get("reasons", []),
                        "kind": "search_filter"
                        if any(
                            "keyword" in str(reason) or "location" in str(reason)
                            for reason in observation.payload.get("reasons", [])
                        )
                        else "link_verification" if observation.error_category.startswith("link_")
                        else "verification",
                    }
                )
            else:
                failures.append({"url": url, "error": observation.error_category, "detail": observation.error_message})
    metrics = RunMetrics(
        run_id=run_id,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        pages_visited=len(state.visited_urls),
        jobs_found=len(state.extracted_jobs),
        valid_jobs=len(jobs),
        failed_pages=len(failures),
        avg_steps_per_job=state.budget.consumed_steps / max(1, len(state.verified_jobs)),
    )
    return WorkflowState(
        user=state.user,
        jobs=jobs,
        pending_jobs=pending_jobs,
        matches=[] if unsaved else state.matches,
        metrics=metrics,
        candidate_urls=state.candidate_urls,
        failed_urls=list(dict.fromkeys(row["url"] for row in failures)),
        metadata={
            "orchestration_mode": "hybrid",
            "agent_trace": trace,
            "unique_candidates": len(set(state.candidate_urls)),
            "open_successes": sum(o.success for o in state.observation_history if o.tool_name is AgentAction.OPEN_PAGE),
            "open_failures": sum(
                not o.success for o in state.observation_history if o.tool_name is AgentAction.OPEN_PAGE
            ),
            "tool_latency_ms": state.metrics.total_latency_ms,
            "runtime_latency_ms": state.metrics.runtime_latency_ms,
            "approval_wait_ms": state.metrics.approval_wait_ms,
            "approval_audit": [event.model_dump(mode="json") for event in state.approval_audit],
            "planner_routes": state.planner_routes,
            "model_usage": state.model_usage,
            "execution_trace": legacy_trace,
            "filtered_jobs": filtered,
            "pending_jobs": [job.model_dump(mode="json") for job in pending_jobs],
            "failed_url_errors": failures,
            "terminal_status": state.terminal_status,
            "terminal_reason": state.terminal_reason,
        },
    )
