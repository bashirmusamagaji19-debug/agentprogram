# Human-in-the-Loop Checkpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable LangGraph approval pause before `save_results`, with cross-process SQLite resume, rejection, redacted audit evidence, and idempotent persistence.

**Architecture:** Keep the existing automatic Hybrid Agent API, but compile an approval-aware graph when an official SQLite checkpointer is supplied. A preparation node persists a redacted request before `interrupt`; resume uses `Command`, and the repository uses the approval ID as a transactional idempotency key so graph replay cannot duplicate the visible save effect.

**Tech Stack:** Python 3.11+, Pydantic 2, LangGraph 1.2, `langgraph-checkpoint-sqlite` 3.1, SQLite, pytest/pytest-asyncio, Ruff.

---

## File Map

- Create `src/web_task_agent/agent_approval.py`: approval request/decision/audit/result contracts and validation errors.
- Create `src/web_task_agent/agent_checkpoint.py`: async SQLite checkpointer lifecycle and path validation.
- Create `src/web_task_agent/agent_hitl_evaluation.py`: deterministic HITL benchmark models, scenarios, and versioned artifact writer.
- Modify `src/web_task_agent/agent_models.py`: add execution ID, HITL mode, pending request, and audit state.
- Modify `src/web_task_agent/storage.py`: transactional idempotency receipt table and `save_jobs_once` API.
- Modify `src/web_task_agent/agent_tools.py`: pass stable idempotency keys through `SaveResultsTool`.
- Modify `src/web_task_agent/agent_policy.py`: match and save before `target_reached` finish.
- Modify `src/web_task_agent/agent_runtime.py`: approval nodes, interrupt/resume APIs, checkpoint-backed graph caching, and explicit resume errors.
- Modify `src/web_task_agent/agent_cli.py`: build checkpoint-aware runtimes and render approval evidence.
- Modify `src/web_task_agent/workflow.py`: expose HITL start while preserving `run_with_hybrid_agent`.
- Modify `src/web_task_agent/cli.py`: flags, validation, start/resume flow, output, and exit codes.
- Modify `src/web_task_agent/agent_planner_benchmark.py`: use a new benchmark version after action-sequence changes.
- Modify `pyproject.toml`: add the official SQLite checkpointer dependency.
- Create `tests/test_agent_approval.py`, `tests/test_agent_checkpoint.py`, and `tests/test_agent_hitl_evaluation.py`.
- Modify `tests/test_storage.py`, `tests/test_agent_tools.py`, `tests/test_agent_policy.py`, `tests/test_agent_runtime.py`, `tests/test_agent_cli.py`, `tests/test_agent_planner_benchmark.py`, and `tests/test_scaffold.py`.
- Modify `README.md`, `docs/mvp-verification.md`, and `docs/project-story.md`.
- Create `docs/work-log/2026-07-29-hitl-checkpoint.md` and versioned files under `docs/results/hitl-checkpoint/`.

### Task 1: Install The Checkpointer And Define Approval Contracts

**Files:**
- Modify: `pyproject.toml`
- Create: `src/web_task_agent/agent_approval.py`
- Modify: `src/web_task_agent/agent_models.py`
- Create: `tests/test_agent_approval.py`

- [ ] **Step 1: Write failing approval contract tests**

```python
from datetime import datetime, timezone

import pytest

from web_task_agent.agent_approval import (
    ApprovalAuditEvent,
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalStatus,
)


def test_approval_request_exposes_only_redacted_summary_fields():
    request = ApprovalRequest(
        approval_id="approval-1",
        thread_id="demo-1",
        requested_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        job_count=2,
        summary="Persist 2 verified job records.",
    )

    assert request.status is ApprovalStatus.PENDING
    assert set(request.public_payload()) == {
        "approval_id",
        "thread_id",
        "action",
        "requested_at",
        "job_count",
        "summary",
        "status",
    }


def test_approval_decision_rejects_blank_id_and_overlong_note():
    with pytest.raises(ValueError, match="approval_id"):
        ApprovalDecision(approval_id=" ", outcome=ApprovalOutcome.APPROVE)
    with pytest.raises(ValueError, match="at most 500"):
        ApprovalDecision(
            approval_id="approval-1",
            outcome=ApprovalOutcome.REJECT,
            note="x" * 501,
        )


def test_audit_event_requires_utc_timestamp():
    with pytest.raises(ValueError, match="timezone-aware"):
        ApprovalAuditEvent(
            approval_id="approval-1",
            event="requested",
            action="save_results",
            occurred_at=datetime(2026, 7, 29),
        )
```

- [ ] **Step 2: Run the tests and verify the missing module failure**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_approval.py -q
```

Expected: collection fails with `ModuleNotFoundError: web_task_agent.agent_approval`.

- [ ] **Step 3: Add the dependency and minimal contracts**

Add to `pyproject.toml` dependencies:

```toml
"langgraph-checkpoint-sqlite>=3.1,<4",
```

Create `agent_approval.py` with these public types and validators:

```python
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApprovalOutcome(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    approval_id: str
    thread_id: str
    action: Literal["save_results"] = "save_results"
    requested_at: datetime
    job_count: int = Field(ge=0)
    summary: str = Field(min_length=1, max_length=200)
    status: ApprovalStatus = ApprovalStatus.PENDING

    @field_validator("approval_id", "thread_id")
    @classmethod
    def require_identifier(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("approval_id and thread_id must not be blank")
        return value

    def public_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    approval_id: str
    outcome: ApprovalOutcome
    note: str = Field(default="", max_length=500)

    @field_validator("approval_id", "note")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("approval_id")
    @classmethod
    def require_approval_id(cls, value: str) -> str:
        if not value:
            raise ValueError("approval_id must not be blank")
        return value


class ApprovalAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    approval_id: str
    event: Literal["requested", "resolved"]
    action: Literal["save_results"] = "save_results"
    occurred_at: datetime
    outcome: ApprovalOutcome | None = None
    note: str = Field(default="", max_length=500)

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value


class HitlRunStatus(StrEnum):
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    PARTIAL = "partial"
    FAILED = "failed"


class HitlRuntimeError(RuntimeError):
    pass
```

Add these fields to `DecisionAgentState`:

```python
execution_id: str = Field(default_factory=lambda: uuid4().hex)
hitl_enabled: bool = False
thread_id: str = ""
pending_approval: ApprovalRequest | None = None
approval_audit: list[ApprovalAuditEvent] = Field(default_factory=list)
```

Import `uuid4`, `ApprovalAuditEvent`, and `ApprovalRequest` explicitly.

- [ ] **Step 4: Install and run focused tests**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pip install -e ".[dev]"
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_approval.py tests/test_models.py -q
..\..\.venv\Scripts\python.exe -m ruff check src/web_task_agent/agent_approval.py src/web_task_agent/agent_models.py tests/test_agent_approval.py
```

Expected: all selected tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 5: Commit the contracts**

```powershell
git add pyproject.toml src/web_task_agent/agent_approval.py src/web_task_agent/agent_models.py tests/test_agent_approval.py
git commit -m "feat: define HITL approval contracts"
```

### Task 2: Make Result Persistence Idempotent

**Files:**
- Modify: `src/web_task_agent/storage.py`
- Modify: `src/web_task_agent/agent_tools.py`
- Modify: `tests/test_storage.py`
- Modify: `tests/test_agent_tools.py`

- [ ] **Step 1: Write failing repository and tool tests**

Add to `tests/test_storage.py`:

```python
def test_save_jobs_once_records_receipt_and_reuses_duplicate_key(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()
    job = make_job(title="AI Agent Intern")

    first = repo.save_jobs_once([job], idempotency_key="approval-1")
    second = repo.save_jobs_once(
        [make_job(title="Changed title")],
        idempotency_key="approval-1",
    )

    assert first.reused is False
    assert first.saved_jobs == 1
    assert second.reused is True
    assert repo.list_jobs()[0].title == "AI Agent Intern"
```

Add to `tests/test_agent_tools.py`:

```python
@pytest.mark.asyncio
async def test_save_results_uses_approval_id_as_idempotency_key():
    repository = RecordingRepository()
    state = DecisionAgentState(
        user=UserProfile(keyword="AI intern"),
        verified_jobs=[make_job()],
    )
    tool = SaveResultsTool(repository)

    observation = await tool.execute(state, {"approval_id": "approval-1"})

    assert repository.idempotency_keys == ["approval-1"]
    assert observation.payload == {"saved_jobs": 1, "reused": False}
    assert state.saved is True
```

- [ ] **Step 2: Verify the new API is absent**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_storage.py::test_save_jobs_once_records_receipt_and_reuses_duplicate_key tests/test_agent_tools.py::test_save_results_uses_approval_id_as_idempotency_key -q
```

Expected: failures mention missing `save_jobs_once` or missing recording fields.

- [ ] **Step 3: Implement one-transaction receipts**

Add this frozen return contract to `storage.py`:

```python
@dataclass(frozen=True)
class SaveReceipt:
    idempotency_key: str
    saved_jobs: int
    reused: bool
```

Create the receipt table in `initialize()`:

```sql
CREATE TABLE IF NOT EXISTS save_receipts (
    idempotency_key TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    saved_jobs INTEGER NOT NULL
)
```

Implement `save_jobs_once` with one connection transaction:

```python
def save_jobs_once(
    self,
    jobs: list[JobPosting],
    *,
    idempotency_key: str,
) -> SaveReceipt:
    key = idempotency_key.strip()
    if not key:
        raise ValueError("idempotency_key must not be blank")
    with self._connect() as conn:
        existing = conn.execute(
            "SELECT saved_jobs FROM save_receipts WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if existing is not None:
            return SaveReceipt(key, int(existing["saved_jobs"]), True)
        self._save_jobs_with_connection(conn, jobs)
        conn.execute(
            "INSERT INTO save_receipts (idempotency_key, created_at, saved_jobs) VALUES (?, ?, ?)",
            (key, datetime.now(timezone.utc).isoformat(), len(jobs)),
        )
    return SaveReceipt(key, len(jobs), False)
```

Move the existing `executemany` body into `_save_jobs_with_connection`; retain `save_jobs()` as a
backward-compatible wrapper. Update `SaveResultsTool` to use:

```python
key = str(arguments.get("approval_id") or f"auto:{state.execution_id}")
receipt = self.repository.save_jobs_once(
    state.verified_jobs,
    idempotency_key=key,
)
state.saved = True
return ToolObservation(
    tool_name=self.name,
    success=True,
    summary=f"Persisted {receipt.saved_jobs} verified jobs.",
    payload={"saved_jobs": receipt.saved_jobs, "reused": receipt.reused},
)
```

- [ ] **Step 4: Run storage and tool tests**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_storage.py tests/test_agent_tools.py -q
..\..\.venv\Scripts\python.exe -m ruff check src/web_task_agent/storage.py src/web_task_agent/agent_tools.py tests/test_storage.py tests/test_agent_tools.py
```

Expected: all selected tests pass; the duplicate key leaves the first row unchanged.

- [ ] **Step 5: Commit idempotent persistence**

```powershell
git add src/web_task_agent/storage.py src/web_task_agent/agent_tools.py tests/test_storage.py tests/test_agent_tools.py
git commit -m "feat: make Agent result saves idempotent"
```

### Task 3: Correct Goal Completion Semantics

**Files:**
- Modify: `src/web_task_agent/agent_policy.py`
- Modify: `tests/test_agent_policy.py`
- Modify: `tests/test_agent_runtime.py`
- Modify: `tests/test_agent_evaluation.py`
- Modify: `tests/test_agent_planner_benchmark.py`

- [ ] **Step 1: Write failing policy sequence tests**

```python
def test_target_requires_scoring_then_saving_then_finish(user, verified_job, match):
    policy = DeterministicAgentPolicy()
    state = DecisionAgentState(user=user, verified_jobs=[verified_job])

    assert policy.decide(state).action is AgentAction.SCORE_MATCH

    state.matches = [match]
    assert policy.decide(state).action is AgentAction.SAVE_RESULTS

    state.saved = True
    decision = policy.decide(state)
    assert decision.action is AgentAction.FINISH
    assert decision.arguments["terminal_reason"] == "target_reached"


def test_exhausted_budget_cannot_claim_target_reached_before_save(user, verified_job):
    state = DecisionAgentState(
        user=user,
        verified_jobs=[verified_job],
        budget=AgentBudget(max_steps=4, consumed_steps=4),
    )

    decision = DeterministicAgentPolicy().decide(state)

    assert decision.action is AgentAction.FINISH
    assert decision.arguments["terminal_reason"] == "budget_exhausted"
```

- [ ] **Step 2: Verify current policy finishes too early**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_policy.py -q
```

Expected: the new sequence test receives `finish` instead of `score_match`.

- [ ] **Step 3: Reorder deterministic decisions**

Place budget enforcement before incomplete target work, then add:

```python
target_reached = len(state.verified_jobs) >= state.user.target_count
if target_reached and not state.matches:
    return self._decision(
        AgentAction.SCORE_MATCH,
        "The verified target is ready for profile matching.",
    )
if target_reached and not state.saved:
    return self._decision(
        AgentAction.SAVE_RESULTS,
        "Matched target results are ready for persistence.",
    )
if target_reached:
    return self._decision(
        AgentAction.FINISH,
        "The requested results were verified, matched, and saved.",
        arguments={"terminal_reason": "target_reached"},
    )
```

Update exact runtime and benchmark action-sequence assertions to include `score_match` and
`save_results`. Do not loosen them to unordered membership assertions.

- [ ] **Step 4: Run all policy-dependent tests**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_policy.py tests/test_agent_runtime.py tests/test_agent_evaluation.py tests/test_agent_planner_benchmark.py -q
```

Expected: all selected tests pass with explicit updated sequences.

- [ ] **Step 5: Commit the semantics fix**

```powershell
git add src/web_task_agent/agent_policy.py tests/test_agent_policy.py tests/test_agent_runtime.py tests/test_agent_evaluation.py tests/test_agent_planner_benchmark.py
git commit -m "fix: require match and save before Agent completion"
```

### Task 4: Add The SQLite Checkpointer Lifecycle

**Files:**
- Create: `src/web_task_agent/agent_checkpoint.py`
- Create: `tests/test_agent_checkpoint.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
import pytest
from langgraph.graph import END, START, StateGraph

from web_task_agent.agent_checkpoint import open_sqlite_checkpointer


def increment(state: dict[str, int]) -> dict[str, int]:
    return {"count": state["count"] + 1}


@pytest.mark.asyncio
async def test_checkpointer_creates_parent_and_survives_reopen(tmp_path):
    path = tmp_path / "nested" / "checkpoints.sqlite"
    config = {"configurable": {"thread_id": "thread-1"}}

    async with open_sqlite_checkpointer(path) as first:
        graph = StateGraph(dict)
        graph.add_node("increment", increment)
        graph.add_edge(START, "increment")
        graph.add_edge("increment", END)
        compiled = graph.compile(checkpointer=first)
        await compiled.ainvoke({"count": 0}, config=config)

    async with open_sqlite_checkpointer(path) as second:
        graph = StateGraph(dict)
        graph.add_node("increment", increment)
        graph.add_edge(START, "increment")
        graph.add_edge("increment", END)
        compiled = graph.compile(checkpointer=second)
        snapshot = await compiled.aget_state(config)

    assert path.exists()
    assert snapshot.values["count"] == 1
```

- [ ] **Step 2: Verify the lifecycle module is missing**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_checkpoint.py -q
```

Expected: collection fails for `web_task_agent.agent_checkpoint`.

- [ ] **Step 3: Implement the async context manager**

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


@asynccontextmanager
async def open_sqlite_checkpointer(
    db_path: str | Path,
) -> AsyncIterator[AsyncSqliteSaver]:
    path = Path(db_path)
    if path.name in {"", ".", ".."}:
        raise ValueError("checkpoint database path must name a file")
    path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        await saver.setup()
        yield saver
```

The context manager is the only owner of the async SQLite connection. CLI and tests must keep the
runtime inside this context and must never store a closed saver globally.

- [ ] **Step 4: Run lifecycle and Windows close checks**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_checkpoint.py -q
..\..\.venv\Scripts\python.exe -m ruff check src/web_task_agent/agent_checkpoint.py tests/test_agent_checkpoint.py
```

Expected: tests pass, then the temporary SQLite file can be reopened and deleted by pytest cleanup.

- [ ] **Step 5: Commit checkpoint lifecycle support**

```powershell
git add src/web_task_agent/agent_checkpoint.py tests/test_agent_checkpoint.py
git commit -m "feat: add async SQLite checkpoint lifecycle"
```

### Task 5: Implement Durable Interrupt And Resume

**Files:**
- Modify: `src/web_task_agent/agent_approval.py`
- Modify: `src/web_task_agent/agent_runtime.py`
- Modify: `src/web_task_agent/agent_cli.py`
- Modify: `src/web_task_agent/workflow.py`
- Modify: `tests/test_agent_runtime.py`
- Modify: `tests/test_agent_cli.py`

- [ ] **Step 1: Write failing runtime integration tests**

Create helpers that initialize a real temporary `JobRepository`, a state containing one verified job,
and a registry with `ScoreMatchTool`, `SaveResultsTool`, and `FinishTool`. Add these tests:

```python
@pytest.mark.asyncio
async def test_hitl_pauses_before_save_without_side_effect(tmp_path):
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    repo = initialized_repository(tmp_path / "jobs.sqlite")
    state = target_ready_state()

    async with open_sqlite_checkpointer(checkpoint_path) as saver:
        runtime = hitl_runtime(repo, saver)
        result = await runtime.start_hitl(state, thread_id="thread-1")

    assert result.status is HitlRunStatus.AWAITING_APPROVAL
    assert result.approval is not None
    assert result.approval.action == "save_results"
    assert repo.list_jobs() == []


@pytest.mark.asyncio
async def test_hitl_approve_resumes_from_another_runtime_once(tmp_path):
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    repo = initialized_repository(tmp_path / "jobs.sqlite")

    async with open_sqlite_checkpointer(checkpoint_path) as saver:
        paused = await hitl_runtime(repo, saver).start_hitl(
            target_ready_state(), thread_id="thread-approve"
        )

    async with open_sqlite_checkpointer(checkpoint_path) as saver:
        runtime = hitl_runtime(repo, saver)
        completed = await runtime.resume_hitl(
            thread_id="thread-approve",
            decision=ApprovalDecision(
                approval_id=paused.approval.approval_id,
                outcome=ApprovalOutcome.APPROVE,
            ),
        )

    assert completed.status is HitlRunStatus.COMPLETED
    assert completed.state.terminal_reason == "target_reached"
    assert len(repo.list_jobs()) == 1
    assert completed.state.observation_history[-2].payload["reused"] is False


@pytest.mark.asyncio
async def test_hitl_reject_never_executes_save(tmp_path):
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    repo = initialized_repository(tmp_path / "jobs.sqlite")
    async with open_sqlite_checkpointer(checkpoint_path) as saver:
        runtime = hitl_runtime(repo, saver)
        paused = await runtime.start_hitl(
            target_ready_state(), thread_id="thread-reject"
        )
        rejected = await runtime.resume_hitl(
            thread_id="thread-reject",
            decision=ApprovalDecision(
                approval_id=paused.approval.approval_id,
                outcome=ApprovalOutcome.REJECT,
                note="Do not persist",
            ),
        )

    assert rejected.status is HitlRunStatus.REJECTED
    assert rejected.state.terminal_reason == "human_denied"
    assert repo.list_jobs() == []
    assert all(
        item.tool_name is not AgentAction.SAVE_RESULTS
        for item in rejected.state.observation_history
    )
```

Also test blank/missing threads, mismatched approval IDs, resume without an interrupt, and resume after
terminal completion. Each must raise `HitlRuntimeError` with a specific message.

- [ ] **Step 2: Verify runtime has no HITL API**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime.py -q
```

Expected: failures mention missing `start_hitl` and `resume_hitl`.

- [ ] **Step 3: Implement graph nodes and runtime result**

Add the runtime result next to `HybridAgentRuntime` in `agent_runtime.py`, avoiding a circular import
between the state and approval model modules:

```python
@dataclass(frozen=True)
class HitlRunResult:
    status: HitlRunStatus
    state: DecisionAgentState
    approval: ApprovalRequest | None = None
```

In `HybridAgentRuntime`, accept `checkpointer=None`, cache the compiled graph, and use:

```python
async def start_hitl(
    self,
    state: DecisionAgentState,
    *,
    thread_id: str,
) -> HitlRunResult:
    thread_id = self._require_thread_id(thread_id)
    self._require_checkpointer()
    state.hitl_enabled = True
    state.thread_id = thread_id
    await self._graph().ainvoke(state, config=self._config(state, thread_id))
    return await self._hitl_result(thread_id)


async def resume_hitl(
    self,
    *,
    thread_id: str,
    decision: ApprovalDecision,
) -> HitlRunResult:
    thread_id = self._require_thread_id(thread_id)
    self._require_checkpointer()
    snapshot = await self._graph().aget_state(self._config(None, thread_id))
    state = self._state_from_snapshot(snapshot, thread_id)
    request = state.pending_approval
    if request is None or request.status is not ApprovalStatus.PENDING:
        raise HitlRuntimeError(f"thread {thread_id!r} has no pending approval")
    if request.approval_id != decision.approval_id:
        raise HitlRuntimeError("approval_id does not match the pending request")
    await self._graph().ainvoke(
        Command(resume=decision.model_dump(mode="json")),
        config=self._config(state, thread_id),
    )
    return await self._hitl_result(thread_id)
```

Build one graph with conditional routing after `decide`:

```python
graph.add_node("prepare_approval", self._prepare_approval_node)
graph.add_node("approval_gate", self._approval_gate_node)
graph.add_node("human_denied", self._human_denied_node)
graph.add_conditional_edges(
    "decide",
    self._route_after_decision,
    {"execute": "execute_tool", "approve": "prepare_approval"},
)
graph.add_edge("prepare_approval", "approval_gate")
graph.add_conditional_edges(
    "approval_gate",
    self._route_after_approval,
    {"execute": "execute_tool", "deny": "human_denied"},
)
graph.add_edge("human_denied", "finish")
```

`_prepare_approval_node` creates `approval-{uuid4().hex}`, assigns `pending_approval`, and appends one
`requested` audit event. `_approval_gate_node` calls `interrupt(request.public_payload())`, validates
the returned `ApprovalDecision`, updates the request status, appends one `resolved` event with
`datetime.now(timezone.utc)`, and injects `approval_id` into `last_decision.arguments` only on approval.
`_human_denied_node` sets `terminal_status="rejected"` and `terminal_reason="human_denied"`.

`_route_after_decision` returns `approve` only when all conditions are true:

```python
return (
    "approve"
    if state.hitl_enabled
    and state.last_decision is not None
    and state.last_decision.action is AgentAction.SAVE_RESULTS
    else "execute"
)
```

Build the graph once with `graph.compile(checkpointer=self.checkpointer)`. Use `aget_state` after each
invoke to construct results, and derive `awaiting_approval` from a pending request plus checkpoint
interrupt tasks rather than from untrusted caller input.

Update `build_hybrid_runtime(workflow, planner=None, checkpointer=None)` and add a workflow
`start_with_hybrid_agent_hitl` wrapper that constructs `DecisionAgentState` exactly as the existing
automatic wrapper does.

- [ ] **Step 4: Run runtime, compatibility, and resource tests**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime.py tests/test_agent_cli.py tests/test_agent_checkpoint.py -q
..\..\.venv\Scripts\python.exe -m ruff check src/web_task_agent/agent_runtime.py src/web_task_agent/agent_cli.py src/web_task_agent/workflow.py tests/test_agent_runtime.py
```

Expected: pause/approve/reject/cross-runtime tests pass, and existing automatic runtime tests remain
green.

- [ ] **Step 5: Commit durable HITL runtime support**

```powershell
git add src/web_task_agent/agent_approval.py src/web_task_agent/agent_runtime.py src/web_task_agent/agent_cli.py src/web_task_agent/workflow.py tests/test_agent_runtime.py tests/test_agent_cli.py
git commit -m "feat: pause and resume Agent saves with LangGraph"
```

### Task 6: Add The CLI Start And Resume Contract

**Files:**
- Modify: `src/web_task_agent/cli.py`
- Modify: `tests/test_agent_cli.py`
- Modify: `tests/test_scaffold.py`

- [ ] **Step 1: Write failing parser and CLI behavior tests**

```python
def test_parser_accepts_hitl_checkpoint_flags():
    args = build_parser().parse_args(
        [
            "--hybrid-agent",
            "--hitl",
            "--thread-id",
            "demo-1",
            "--checkpoint-db",
            ".agent/checkpoints.sqlite",
            "--resume-approval",
            "approve",
            "--approval-id",
            "approval-1",
            "--approval-note",
            "Reviewed",
        ]
    )

    assert args.hitl is True
    assert args.resume_approval == "approve"
    assert args.approval_id == "approval-1"


@pytest.mark.asyncio
async def test_cli_hitl_pause_prints_resume_identity(monkeypatch, capsys, tmp_path):
    args = build_parser().parse_args(
        [
            "--demo",
            "--hybrid-agent",
            "--hitl",
            "--thread-id",
            "demo-1",
            "--checkpoint-db",
            str(tmp_path / "checkpoints.sqlite"),
            "--db-path",
            str(tmp_path / "jobs.sqlite"),
            "--keyword",
            "AI intern",
            "--target-count",
            "1",
        ]
    )

    exit_code = await cli_module._run(args)
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "awaiting_approval" in output
    assert "Thread ID: demo-1" in output
    assert "--resume-approval approve" in output
```

Add tests asserting exit code `2` for `--hitl` without `--hybrid-agent`, missing thread ID, resume
without approval ID, and resume combined with seed/profile arguments.

- [ ] **Step 2: Verify the parser rejects unknown flags**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_cli.py::test_parser_accepts_hitl_checkpoint_flags -q
```

Expected: argparse exits because `--hitl` and related flags are unknown.

- [ ] **Step 3: Add flags, validation, and the runtime context**

Add parser options:

```python
parser.add_argument("--hitl", action="store_true")
parser.add_argument("--thread-id")
parser.add_argument("--checkpoint-db", default=".agent/checkpoints.sqlite")
parser.add_argument("--resume-approval", choices=["approve", "reject"])
parser.add_argument("--approval-id")
parser.add_argument(
    "--approval-note",
    default="",
    help="Public audit note attached to the approval decision.",
)
```

Implement `validate_hitl_args(args) -> str | None` and call it before browser/provider construction.
For HITL start/resume, keep all runtime calls inside:

```python
async with open_sqlite_checkpointer(args.checkpoint_db) as saver:
    runtime = build_hybrid_runtime(
        workflow,
        planner=planner,
        checkpointer=saver,
    )
    if args.resume_approval:
        result = await runtime.resume_hitl(
            thread_id=args.thread_id,
            decision=ApprovalDecision(
                approval_id=args.approval_id,
                outcome=ApprovalOutcome(args.resume_approval),
                note=args.approval_note,
            ),
        )
    else:
        result = await workflow.start_with_hybrid_agent_hitl(
            user,
            runtime=runtime,
            thread_id=args.thread_id,
            max_steps=args.agent_max_steps,
        )
```

Print status, thread ID, approval ID, redacted summary, and both resume commands for a pause. Return
`0` for `awaiting_approval`, `completed`, and intentional `rejected`; return `2` for invalid input,
runtime configuration errors, `partial`, and `failed`.

Resume mode must not read resume files or construct a replacement `UserProfile`. Reject explicitly
provided `--keyword`, `--seed-url`, `--resume-text`, `--resume-file`, `--target-count`, `--skill`, or
planner overrides by inspecting the raw argument list in `main` and storing the explicitly supplied
option names on the namespace.

- [ ] **Step 4: Run parser, end-to-end CLI, and help tests**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_cli.py tests/test_scaffold.py -q
..\..\.venv\Scripts\web-task-agent.exe --help
```

Expected: tests pass; help lists HITL flags and labels approval notes as public audit data.

- [ ] **Step 5: Commit the CLI flow**

```powershell
git add src/web_task_agent/cli.py tests/test_agent_cli.py tests/test_scaffold.py
git commit -m "feat: expose HITL pause and resume in CLI"
```

### Task 7: Render Redacted Approval Evidence

**Files:**
- Modify: `src/web_task_agent/agent_cli.py`
- Modify: `tests/test_agent_cli.py`

- [ ] **Step 1: Write failing JSON and Markdown evidence tests**

```python
def test_hybrid_artifacts_render_redacted_approval_audit():
    state = _completed_state()
    state.thread_id = "demo-1"
    state.approval_audit = [
        ApprovalAuditEvent(
            approval_id="approval-1",
            event="requested",
            occurred_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        ),
        ApprovalAuditEvent(
            approval_id="approval-1",
            event="resolved",
            occurred_at=datetime(2026, 7, 29, 0, 1, tzinfo=timezone.utc),
            outcome=ApprovalOutcome.APPROVE,
            note="Reviewed summary",
        ),
    ]

    payload = hybrid_state_payload(state)
    markdown = render_hybrid_markdown(state)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["thread_id"] == "demo-1"
    assert payload["approval_audit"][1]["outcome"] == "approve"
    assert "## Approval Audit" in markdown
    assert "Reviewed summary" in markdown
    for forbidden in ["PRIVATE RESUME", "Authorization", "Bearer ", "api_key"]:
        assert forbidden not in serialized
```

- [ ] **Step 2: Verify approval evidence is absent**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_cli.py::test_hybrid_artifacts_render_redacted_approval_audit -q
```

Expected: payload lacks `thread_id` or `approval_audit`.

- [ ] **Step 3: Extend the shared payload and renderer**

Add to `hybrid_state_payload`:

```python
"thread_id": state.thread_id or None,
"hitl_status": (
    "awaiting_approval"
    if state.pending_approval is not None
    and state.pending_approval.status is ApprovalStatus.PENDING
    else state.terminal_status
),
"pending_approval": (
    state.pending_approval.public_payload()
    if state.pending_approval is not None
    else None
),
"approval_audit": [
    event.model_dump(mode="json") for event in state.approval_audit
],
```

Render a Markdown `Approval Audit` table with event, action, outcome, note, and UTC time. Escape pipes
in the note. Add the same compact table to HTML with `html.escape`; never render checkpoint internals.

- [ ] **Step 4: Run artifact and privacy tests**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_cli.py -q
..\..\.venv\Scripts\python.exe -m ruff check src/web_task_agent/agent_cli.py tests/test_agent_cli.py
```

Expected: all artifact tests pass and no raw secret-like fields appear.

- [ ] **Step 5: Commit approval observability**

```powershell
git add src/web_task_agent/agent_cli.py tests/test_agent_cli.py
git commit -m "feat: publish redacted approval audit evidence"
```

### Task 8: Version And Run HITL Evaluation

**Files:**
- Create: `src/web_task_agent/agent_hitl_evaluation.py`
- Create: `tests/test_agent_hitl_evaluation.py`
- Modify: `src/web_task_agent/agent_planner_benchmark.py`
- Modify: `src/web_task_agent/cli.py`
- Modify: `tests/test_agent_planner_benchmark.py`
- Modify: `tests/test_agent_cli.py`
- Create: `docs/results/hitl-checkpoint/hitl-checkpoint.json`
- Create: `docs/results/hitl-checkpoint/hitl-checkpoint.md`

- [ ] **Step 1: Write failing evaluation and version tests**

```python
def test_hitl_evaluation_reports_protected_effects():
    result = evaluate_hitl_cases(
        [
            HitlCaseResult("approve", paused=True, approved=True, saved_effects=1),
            HitlCaseResult("reject", paused=True, rejected=True, saved_effects=0),
            HitlCaseResult("replay", paused=True, approved=True, saved_effects=1, replayed=True),
        ]
    )

    assert result.benchmark_version == "hybrid-agent-hitl-v1"
    assert result.pause_rate == 1.0
    assert result.rejected_effects == 0
    assert result.duplicate_effects == 0


def test_planner_benchmark_uses_new_version_without_overwriting_v1():
    matrix = PlannerBenchmarkMatrix(providers=[])
    assert matrix.benchmark_version == "hybrid-agent-planner-controlled-v2"
```

- [ ] **Step 2: Verify evaluation module and v2 version are absent**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_hitl_evaluation.py tests/test_agent_planner_benchmark.py -q
```

Expected: missing HITL evaluation module and old planner version assertion failures.

- [ ] **Step 3: Implement versioned evaluation artifacts**

Define frozen Pydantic models with these fields:

```python
class HitlCaseResult(BaseModel):
    case_id: str
    paused: bool
    approved: bool = False
    rejected: bool = False
    saved_effects: int = Field(ge=0)
    replayed: bool = False


class HitlEvaluationResult(BaseModel):
    benchmark_version: str = "hybrid-agent-hitl-v1"
    scope: str = "deterministic checkpoint and persistence scenarios"
    cases: list[HitlCaseResult]
    pause_rate: float
    rejected_effects: int
    duplicate_effects: int
```

`evaluate_hitl_cases` computes rates from actual case records. `write_hitl_evaluation_artifacts` writes
`hitl-checkpoint.json` and `hitl-checkpoint.md` to a caller-provided directory. The Markdown explicitly
states that the scenarios are deterministic fixtures and do not measure real-site extraction.

Add `--hitl-benchmark` and `--hitl-benchmark-output-dir` (default
`docs/results/hitl-checkpoint`) to run approve, reject, and replay scenarios against temporary SQLite
files, then publish only the summarized redacted artifacts.

Change `PLANNER_BENCHMARK_VERSION` to `hybrid-agent-planner-controlled-v2` and change the default
planner output directory to `docs/results/planner-benchmark-v2`. Do not delete or rewrite
`docs/results/planner-benchmark/`.

- [ ] **Step 4: Run evaluation and inspect generated evidence**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_hitl_evaluation.py tests/test_agent_planner_benchmark.py tests/test_agent_cli.py -q
..\..\.venv\Scripts\web-task-agent.exe --hitl-benchmark --hitl-benchmark-output-dir docs/results/hitl-checkpoint
Get-Content docs/results/hitl-checkpoint/hitl-checkpoint.md
```

Expected: three scenarios pause; reject writes zero effects; replay produces no duplicate effect;
Markdown identifies fixture scope. Run provider planner v2 only when the provider is actually
configured, and preserve a skipped status otherwise.

- [ ] **Step 5: Commit versioned evidence**

```powershell
git add src/web_task_agent/agent_hitl_evaluation.py src/web_task_agent/agent_planner_benchmark.py src/web_task_agent/cli.py tests/test_agent_hitl_evaluation.py tests/test_agent_planner_benchmark.py tests/test_agent_cli.py docs/results/hitl-checkpoint
git commit -m "feat: add versioned HITL evaluation evidence"
```

### Task 9: Document, Verify, And Prepare Review

**Files:**
- Modify: `README.md`
- Modify: `docs/mvp-verification.md`
- Modify: `docs/project-story.md`
- Create: `docs/work-log/2026-07-29-hitl-checkpoint.md`

- [ ] **Step 1: Write the documentation acceptance assertions**

Add to `tests/test_scaffold.py`:

```python
def test_public_docs_explain_hitl_checkpoint_boundaries():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    story = (root / "docs" / "project-story.md").read_text(encoding="utf-8")
    verification = (root / "docs" / "mvp-verification.md").read_text(encoding="utf-8")
    combined = "\n".join([readme, story, verification])

    assert "Human-in-the-loop" in combined
    assert "thread_id" in combined
    assert "human_denied" in combined
    assert "langgraph-checkpoint-sqlite" in combined
    assert "不需要 GPU" in combined
```

- [ ] **Step 2: Verify docs do not yet meet the contract**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_scaffold.py::test_public_docs_explain_hitl_checkpoint_boundaries -q
```

Expected: assertion failure for missing HITL documentation.

- [ ] **Step 3: Update public docs and create the work log**

Document these exact claims, backed by final artifacts only:

- `save_results` pauses before side effects and resumes by stable `thread_id`;
- approve persists idempotently; reject terminates as `human_denied` without saving;
- SQLite supports cross-process recovery and is not long-term semantic memory;
- checkpoint replay plus repository receipt closes the duplicate-effect crash window;
- benchmark scenarios are deterministic fixtures, not real-site extraction quality;
- provider v2 results are listed only if rerun successfully;
- no cloud server, GPU, or training is required.

The work log must include: motivation, architecture decision, files changed, commands run, final test and
coverage evidence, benchmark scope, limitations, interview talking points, branch name, and commit IDs.

- [ ] **Step 4: Run complete verification from a clean process**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest
..\..\.venv\Scripts\python.exe -m pytest --cov=web_task_agent --cov-report=term-missing
..\..\.venv\Scripts\python.exe -m ruff check .
..\..\.venv\Scripts\python.exe -m pip wheel . --no-deps --wheel-dir dist
..\..\.venv\Scripts\web-task-agent.exe --doctor
git diff --check origin/master...HEAD
git status --short
```

Expected: all tests pass, coverage remains at least 70%, Ruff passes, wheel/sdist build succeeds,
doctor checks succeed, diff check is clean, and only intended documentation changes remain before the
final commit.

- [ ] **Step 5: Commit final documentation**

```powershell
git add README.md docs/mvp-verification.md docs/project-story.md docs/work-log/2026-07-29-hitl-checkpoint.md tests/test_scaffold.py
git commit -m "docs: explain HITL checkpoint evidence"
```

- [ ] **Step 6: Re-run release checks and inspect the branch**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest
..\..\.venv\Scripts\python.exe -m ruff check .
git log --oneline --decorate origin/master..HEAD
git status --short --branch
```

Expected: tests and Ruff pass, the branch contains small ordered commits, and the worktree is clean.

Do not push, open a pull request, merge, or delete any worktree until the user explicitly approves that
external Git step.
