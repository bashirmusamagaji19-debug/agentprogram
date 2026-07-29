# Human-in-the-Loop Checkpoint Design

## 1. Goal

Extend the Hybrid Decision Agent with a real LangGraph human-in-the-loop boundary:

- pause before the externally visible `save_results` action;
- persist the pause in SQLite;
- resume from another runtime or process using a stable `thread_id`;
- approve the original action exactly once or reject it without saving;
- expose the complete approval lifecycle as interview-ready evidence.

This feature demonstrates Agent application engineering rather than model training. It requires no
cloud server, GPU, fine-tuning, or new model provider.

## 2. Scope

### In scope

- LangGraph `interrupt` and `Command(resume=...)` integration.
- An official SQLite LangGraph checkpointer through `langgraph-checkpoint-sqlite`.
- An approval gate for `save_results`.
- Durable approval requests, decisions, notes, and audit events.
- A stable CLI `thread_id` and cross-process resume commands.
- Idempotent persistence keyed by `approval_id`.
- JSON and Markdown evidence for paused, approved, and rejected runs.
- Deterministic tests for pause, resume, rejection, idempotency, and compatibility.
- Versioned benchmark evidence after the action sequence changes.

### Out of scope

- A web approval console, authentication service, or multi-user authorization model.
- Approval gates for read-only tools.
- Arbitrary edits to tool arguments during approval.
- Distributed checkpoint storage or high-availability deployment.
- Model training, fine-tuning, or GPU infrastructure.
- Autonomous job application submission.

## 3. Runtime Modes

The current automatic API remains available:

```python
await runtime.run(state)
```

It compiles and runs the graph without an approval checkpointer. This preserves existing callers and
keeps deterministic CI independent of a checkpoint database.

The new HITL API has two explicit operations:

```python
await runtime.start_hitl(state, thread_id=thread_id)
await runtime.resume_hitl(thread_id=thread_id, decision=decision)
```

`start_hitl` runs until completion or an interrupt. `resume_hitl` loads the existing checkpoint and
supplies a validated approval decision. HITL mode owns a compiled graph connected to a long-lived
SQLite checkpointer; it must not rebuild an uncheckpointed graph for each call.

Both operations return an application-level result with one of these statuses:

- `awaiting_approval`
- `completed`
- `rejected`
- `partial`
- `failed`

The result contains the current Agent state and, when paused, the public approval request.

## 4. Graph Architecture

The graph adds a durable preparation node before the interrupt:

```text
initialize -> decide -> route_action
                         | ordinary action
                         v
                    execute_tool -> observe -> guard -> decide
                         ^
                         | approved
                         |
                    approval_gate
                         ^
                         |
                    prepare_approval
                         ^
                         | save_results
                         |
                    route_action

approval_gate -> human_denied -> finish  (rejected)
guard -> finish                          (terminal)
```

`prepare_approval` creates and persists the approval request before the graph reaches the interrupt.
This prevents resume-time node replay from creating a different request. `approval_gate` calls
LangGraph `interrupt` with a redacted payload. On resume it validates the supplied decision and routes
to either the original tool execution or `human_denied`.

The approval decision never comes from the LLM planner. Code owns the approval gate regardless of
whether the `save_results` decision source is `policy`, `llm`, or `fallback`.

## 5. Goal Completion Order

The current deterministic policy finishes as soon as `verified_jobs >= target_count`, so normal runs
can skip both matching and persistence. The policy must instead use this order once the target is met:

1. Run `score_match` when matches do not exist.
2. Select `save_results` when results are not saved.
3. In HITL mode, pause before executing `save_results`.
4. Finish with `target_reached` only after persistence succeeds.

The step budget still applies to actual tools, including scoring and saving. An approval pause or
resume command does not consume a tool step. If the budget is exhausted before a required tool, the
run terminates as `partial` with `budget_exhausted`; reaching the verified-job count alone is not a
completed task.

This intentionally changes the final action sequence. Compatibility means the old `run()` entry point
still works without human input, not that obsolete action sequences remain unchanged.

## 6. State And Contracts

### Approval request

`ApprovalRequest` contains:

- `approval_id`: stable identifier generated once in `prepare_approval`.
- `thread_id`: checkpoint thread identifier.
- `action`: always `save_results` in this release.
- `requested_at`: UTC timestamp.
- `job_count`: number of verified jobs to save.
- `summary`: bounded, redacted description of the pending effect.
- `status`: `pending`, `approved`, or `rejected`.

The interrupt payload may contain only these public fields. It must not contain resume text, raw page
content, provider prompts, credentials, environment variables, or API keys.

### Approval decision

`ApprovalDecision` contains:

- `approval_id`.
- `outcome`: `approve` or `reject`.
- `note`: optional, whitespace-trimmed, and length limited.
- `resolved_at`: UTC timestamp assigned by the application.

The resumed `approval_id` must match the pending request. The caller cannot change the action, tool
arguments, selected jobs, or thread identifier.

### Audit trail

The Agent state gains an append-only approval audit list. It records two events:

- `requested`: approval ID, action, timestamp, and redacted summary.
- `resolved`: approval ID, outcome, timestamp, and optional note.

The request itself remains in state so a newly started process can inspect the pending action before
resuming it.

## 7. SQLite And Exactly-Once Effects

Checkpoint data and job data remain separate:

- checkpoint database: default `.agent/checkpoints.sqlite`;
- application database: existing `--db-path` value.

LangGraph checkpoints provide durable pause and replay, but a process can fail after the business
database commit and before the next checkpoint commit. Checkpointing alone therefore cannot guarantee
an exactly-once external effect.

`SaveResultsTool` must receive the `approval_id` as an idempotency key. `JobRepository` records a save
receipt with a unique `approval_id` in the same SQLite transaction as the result writes. A replay with
the same approval ID becomes a successful no-op and reports that the prior effect was reused. This
provides exactly-once observable persistence across duplicate resumes and crash recovery.

Automatic non-HITL runs use a run-scoped idempotency key so their existing interface remains usable.

## 8. CLI Contract

Start a pausable run:

```powershell
web-task-agent --hybrid-agent --hitl `
  --thread-id interview-demo-001 `
  --checkpoint-db .agent/checkpoints.sqlite `
  --demo --keyword "AI Agent intern" --target-count 1
```

Approve and resume:

```powershell
web-task-agent --hybrid-agent --hitl `
  --thread-id interview-demo-001 `
  --checkpoint-db .agent/checkpoints.sqlite `
  --resume-approval approve `
  --approval-note "Reviewed result summary"
```

Reject and resume:

```powershell
web-task-agent --hybrid-agent --hitl `
  --thread-id interview-demo-001 `
  --checkpoint-db .agent/checkpoints.sqlite `
  --resume-approval reject `
  --approval-note "Do not persist this run"
```

`--resume-approval` uses one enum-valued option, so approve and reject are mutually exclusive. Resume
mode rejects initial-run-only arguments that could alter the checkpointed goal. A paused run exits
successfully after printing the thread ID, approval ID, redacted summary, and exact resume command.

## 9. Rejection And Error Handling

Approval rejection must:

- skip `SaveResultsTool` completely;
- append the resolved audit event;
- set `terminal_status` to `rejected`;
- set `terminal_reason` to `human_denied`;
- route directly to finish.

The runtime and CLI provide explicit errors for:

- missing or blank `thread_id`;
- checkpoint thread not found;
- thread has no pending interrupt;
- resume attempted after completion or rejection;
- mismatched or malformed `approval_id`;
- unsupported approval outcome;
- resume arguments that attempt to alter the original action;
- unreadable or unwritable checkpoint database;
- checkpoint schema or connection failure.

These are caller/configuration errors, not planner fallbacks. They must not create a new run silently.

## 10. Evidence And Privacy

The JSON artifact remains the source of truth and adds:

- `thread_id`;
- current HITL status;
- redacted pending request, when present;
- approval audit events;
- idempotency receipt outcome;
- the updated decision/tool sequence.

The Markdown report renders an `Approval Audit` section from the same payload. Paused runs may produce
intermediate artifacts, and resumed runs produce a final artifact linked by the same thread ID.

No public artifact may include resume text, full page content, API keys, or checkpoint-internal binary
data. Approval notes are considered public evidence and the CLI help must say so.

## 11. Testing Strategy

### Contract and state tests

- validate approval IDs, outcomes, note bounds, timestamps, and redaction;
- reject mismatched approval payloads;
- serialize and restore the new Pydantic state through LangGraph.

### Runtime tests

- pause before `save_results` and assert zero persistence side effects;
- approve and assert the original save executes once;
- reject and assert save never executes;
- assert approval does not consume a tool step;
- assert the LLM planner cannot bypass or resolve the gate;
- preserve the non-HITL `run()` entry point.

### Persistence and recovery tests

- pause with runtime A, close it, and resume from runtime B using the same SQLite file;
- simulate duplicate resume/replay and verify the idempotency receipt prevents a second write;
- reject missing, completed, rejected, and non-interrupted threads;
- verify checkpoint and repository connections close cleanly on Windows.

### CLI and artifact tests

- test initial pause, approve, and reject commands;
- verify useful errors for invalid argument combinations;
- assert JSON and Markdown show the same redacted audit lifecycle;
- assert secret-like source fields never enter approval output.

The complete existing test suite, Ruff, and coverage threshold remain mandatory.

## 12. Benchmark Versioning

The policy change adds `score_match` and `save_results` to successful paths, so historical planner
step counts and fallback ratios are no longer directly comparable. Existing benchmark JSON and
Markdown files must not be overwritten.

The implementation creates a new versioned HITL benchmark artifact that reports:

- pause rate at protected actions;
- approval and rejection completion behavior;
- duplicate-effect prevention;
- action sequence and step count under the new policy;
- deterministic, DeepSeek, and Qwen results only when each provider was actually rerun.

If provider credentials are unavailable, the release reports deterministic HITL results and clearly
marks provider reruns as not executed. It must not copy historical provider numbers into the new
artifact as if they were current.

## 13. Documentation And Work Log

The implementation round updates:

- `README.md` with the pause/resume demo;
- `docs/mvp-verification.md` with reproducible commands and final evidence;
- `docs/project-story.md` with the interview explanation and trade-offs;
- a dated file under `docs/work-log/` containing rationale, files changed, verification, limitations,
  and interview talking points;
- versioned JSON and Markdown benchmark artifacts.

Changes are divided into small, reversible commits. No pull request is merged without explicit user
approval.

## 14. External Resources And Human Actions

No cloud server, GPU, training job, or model fine-tuning is required.

Human actions are limited to:

- approving or rejecting the CLI demo when demonstrating the HITL flow;
- providing `DEEPSEEK_API_KEY` or `DASHSCOPE_API_KEY` only if real planner benchmarks are rerun;
- completing GitHub authentication if push or pull-request creation requires it;
- reviewing final resume wording and public evidence.

The deterministic implementation, tests, and checkpoint demo must work without provider keys.

## 15. Acceptance Criteria

- A run pauses durably before `save_results` and survives process restart.
- The interrupt payload contains only approved redacted fields.
- Approving executes the original persistence effect exactly once.
- Rejecting never invokes persistence and ends with `human_denied`.
- Missing, invalid, duplicate, and completed-thread resumes fail explicitly.
- Approval decisions are code-controlled and cannot be supplied by the planner.
- The automatic `run()` API remains available without human input.
- Checkpoint and repository resources close cleanly on Windows.
- JSON and Markdown contain the same requested/resolved audit evidence.
- Historical benchmark artifacts remain unchanged and new metrics are evidence-bound.
- The full suite, Ruff, coverage, README, verification guide, project story, and work log are current.
- No cloud training or manual data-labeling work is required.
