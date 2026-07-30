from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel, Field

from web_task_agent.agent_approval import ApprovalDecision, ApprovalOutcome
from web_task_agent.agent_checkpoint import open_sqlite_checkpointer
from web_task_agent.agent_models import AgentBudget, DecisionAgentState
from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.agent_runtime import HybridAgentRuntime
from web_task_agent.agent_tools import (
    AgentToolRegistry,
    FinishTool,
    SaveResultsTool,
    ScoreMatchTool,
)
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import JobPosting, UserProfile
from web_task_agent.storage import JobRepository


class HitlCaseResult(BaseModel):
    case_id: str
    paused: bool
    approved: bool = False
    rejected: bool = False
    saved_effects: int = Field(ge=0)
    replayed: bool = False
    terminal_reason: str = ""


class HitlEvaluationResult(BaseModel):
    benchmark_version: str = "hybrid-agent-hitl-v1"
    scope: str = "deterministic checkpoint and persistence scenarios"
    cases: list[HitlCaseResult]
    pause_rate: float = Field(ge=0, le=1)
    rejected_effects: int = Field(ge=0)
    duplicate_effects: int = Field(ge=0)


def evaluate_hitl_cases(cases: list[HitlCaseResult]) -> HitlEvaluationResult:
    total = len(cases)
    return HitlEvaluationResult(
        cases=cases,
        pause_rate=(sum(case.paused for case in cases) / total if total else 0.0),
        rejected_effects=sum(case.saved_effects for case in cases if case.rejected),
        duplicate_effects=sum(
            max(case.saved_effects - 1, 0) for case in cases if case.replayed
        ),
    )


def render_hitl_evaluation_markdown(result: HitlEvaluationResult) -> str:
    lines = [
        "# Hybrid Agent HITL Checkpoint Evaluation",
        "",
        f"- Version: `{result.benchmark_version}`",
        f"- Scope: {result.scope}",
        f"- Pause rate: {result.pause_rate:.2f}",
        f"- Rejected-path effects: {result.rejected_effects}",
        f"- Duplicate effects: {result.duplicate_effects}",
        "",
        (
            "This deterministic checkpoint benchmark uses controlled fixtures and "
            "does not measure real-site extraction quality."
        ),
        "",
        "| Case | Paused | Approved | Rejected | Replayed | Saved effects | Terminal reason |",
        "|---|---|---|---|---|---:|---|",
    ]
    for case in result.cases:
        lines.append(
            f"| {case.case_id} | {str(case.paused).lower()} | "
            f"{str(case.approved).lower()} | {str(case.rejected).lower()} | "
            f"{str(case.replayed).lower()} | {case.saved_effects} | "
            f"{case.terminal_reason} |"
        )
    return "\n".join(lines) + "\n"


def write_hitl_evaluation_artifacts(
    result: HitlEvaluationResult,
    output_dir: str | Path,
) -> dict[str, Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "hitl-checkpoint.json"
    markdown_path = destination / "hitl-checkpoint.md"
    json_path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_hitl_evaluation_markdown(result),
        encoding="utf-8",
    )
    return {"json": json_path, "markdown": markdown_path}


async def run_hitl_evaluation(output_dir: str | Path) -> HitlEvaluationResult:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    cases = [
        await _run_checkpoint_case(destination, case_id="approve", approve=True),
        await _run_checkpoint_case(destination, case_id="reject", approve=False),
        await _run_checkpoint_case(
            destination,
            case_id="replay",
            approve=True,
            replay=True,
        ),
    ]
    return evaluate_hitl_cases(cases)


async def _run_checkpoint_case(
    destination: Path,
    *,
    case_id: str,
    approve: bool,
    replay: bool = False,
) -> HitlCaseResult:
    with TemporaryDirectory(prefix=f"hitl-{case_id}-", dir=destination) as temp_dir:
        root = Path(temp_dir)
        checkpoint_path = root / "checkpoints.sqlite"
        repository = JobRepository(root / "jobs.sqlite")
        repository.initialize()
        job = _benchmark_job()
        state = DecisionAgentState(
            user=UserProfile(
                keyword="AI agent intern",
                target_count=1,
                skills=["Python", "LangGraph"],
            ),
            budget=AgentBudget(max_steps=6),
            verified_jobs=[job],
        )
        thread_id = f"hitl-benchmark-{case_id}"
        async with open_sqlite_checkpointer(checkpoint_path) as saver:
            paused = await _runtime(repository, saver).start_hitl(
                state,
                thread_id=thread_id,
            )
        approval = paused.approval
        if approval is None:
            raise RuntimeError(f"{case_id} did not produce an approval request")
        outcome = ApprovalOutcome.APPROVE if approve else ApprovalOutcome.REJECT
        async with open_sqlite_checkpointer(checkpoint_path) as saver:
            completed = await _runtime(repository, saver).resume_hitl(
                thread_id=thread_id,
                decision=ApprovalDecision(
                    approval_id=approval.approval_id,
                    outcome=outcome,
                    note=f"benchmark {case_id}",
                ),
            )
        if replay:
            receipt = repository.save_jobs_once(
                [job],
                idempotency_key=approval.approval_id,
            )
            if not receipt.reused:
                raise RuntimeError("replay did not reuse the idempotency receipt")
        return HitlCaseResult(
            case_id=case_id,
            paused=True,
            approved=approve,
            rejected=not approve,
            saved_effects=len(repository.list_jobs()),
            replayed=replay,
            terminal_reason=completed.state.terminal_reason,
        )


def _runtime(repository: JobRepository, checkpointer) -> HybridAgentRuntime:
    return HybridAgentRuntime(
        registry=AgentToolRegistry(
            [
                ScoreMatchTool(JobMatcher()),
                SaveResultsTool(repository),
                FinishTool(),
            ]
        ),
        policy=DeterministicAgentPolicy(),
        checkpointer=checkpointer,
    )


def _benchmark_job() -> JobPosting:
    return JobPosting(
        title="AI Engineering Intern",
        company="Example AI",
        location="Remote",
        source="fixture",
        url="https://example.com/jobs/1",
        requirements="Python, LangGraph",
        responsibilities="Build AI agents",
        skills=["Python", "LangGraph"],
        confidence=0.9,
    )
