# Benchmark and Explainability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current real-site sample run into a stable benchmark with clear failure explanations in JSON, Markdown reports, and work logs.

**Architecture:** Keep the existing `EvaluationRunner` and `WebTaskWorkflow` flow, but make the benchmark output more explicit: capture verifier-filtered job reasons in workflow metadata, propagate them into evaluation artifacts, and pin the benchmark sample set and report wording so future runs are comparable. Do not change the verifier policy in this plan.

**Tech Stack:** Python 3.11+, pytest, Pydantic, Markdown reports, CLI evaluation commands, existing `web_task_agent` workflow modules.

---

## File Structure

- `src/web_task_agent/workflow.py`: capture filtered job reasons during verification and record them in workflow metadata and execution trace.
- `src/web_task_agent/evaluation.py`: surface filtered-job reasons in task-level evaluation JSON and Markdown summary tables.
- `src/web_task_agent/reporter.py`: keep report wording aligned with the benchmark explanation fields already emitted by evaluation.
- `src/web_task_agent/cli.py`: keep `--real-site-sample` benchmark commands pointed at the same sample corpus and provider matrix.
- `tests/test_scaffold.py`: regression coverage for benchmark outputs, filtered-job explanation fields, and CLI wiring.
- `docs/mvp-verification.md`: record the verified benchmark commands and the observed 3/4 vs 4/4 result.
- `docs/project-story.md`: describe the benchmark as a stable interview-facing artifact, not just a smoke test.
- `README.md`: keep the user-facing benchmark description in sync with the verified behavior.
- `docs/work-log/2026-06-23-real-site-sample-4-run.md`: append the explanation-focused notes for this round.

## Scope Boundary

This plan covers benchmark hardening and explainability only. It does not change verifier rules, does not add new sample URLs, and does not attempt to make `HttpPageLoader` asynchronous. Those can be separate follow-up plans if needed.

---

### Task 1: Record verifier-filtered jobs in workflow metadata

**Files:**
- Modify: `D:\Agent\src\web_task_agent\workflow.py`
- Test: `D:\Agent\tests\test_scaffold.py`

- [ ] **Step 1: Add a regression test for filtered-job metadata**

Add a test that runs the existing real-site sample path and asserts the workflow metadata or returned JSON includes a `filtered_jobs` entry for the failed Anthropic sample, with the job title, company, and reasons.

- [ ] **Step 2: Update the verifier node to capture filtered jobs**

Change `_verifier_node` so it stores filtered jobs in `state.metadata["filtered_jobs"]` as a list of dicts with at least `title`, `company`, `url`, and `reasons`. Keep `state.jobs` unchanged for valid jobs only.

- [ ] **Step 3: Update the verifier trace summary**

Change the verifier trace line from a duplicate-removal summary into a short summary that also mentions how many jobs were filtered by verification.

- [ ] **Step 4: Run the focused tests**

Run:

```powershell
pytest -q tests/test_scaffold.py -k filtered_job
```

Expected: the new filtered-job test passes.

---

### Task 2: Propagate explanation fields into benchmark outputs

**Files:**
- Modify: `D:\Agent\src\web_task_agent\evaluation.py`
- Modify: `D:\Agent\src\web_task_agent\reporter.py`
- Test: `D:\Agent\tests\test_scaffold.py`

- [ ] **Step 1: Add a regression test for evaluation JSON details**

Add a test that loads the `evaluations/real-site-4.json` output shape and asserts the failed task includes the filtered-job reason text, not just `verification_filtered`.

- [ ] **Step 2: Extend evaluation failure formatting**

Teach `_format_failed_url_errors` or a new helper to prefer `filtered_jobs` details when present, so the task-level `failure_details` string explains the specific filtered job and verifier reasons.

- [ ] **Step 3: Keep the Markdown evaluation report aligned**

Ensure the report table shows the same `failure_details` text that appears in JSON, so the benchmark can be inspected without opening multiple artifacts.

- [ ] **Step 4: Run the evaluation tests**

Run:

```powershell
pytest -q tests/test_scaffold.py
```

Expected: all scaffold tests pass, including the benchmark explanation assertions.

---

### Task 3: Refresh benchmark documentation and work log

**Files:**
- Modify: `D:\Agent\docs\mvp-verification.md`
- Modify: `D:\Agent\docs\project-story.md`
- Modify: `D:\Agent\README.md`
- Modify: `D:\Agent\docs\work-log\2026-06-23-real-site-sample-4-run.md`

- [ ] **Step 1: Update the verified command list**

Record the exact benchmark commands that were run successfully:

```powershell
.\.venv\Scripts\web-task-agent.exe --evaluate --real-site-sample --evaluation-count 4 --json-output evaluations\real-site-4.json
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --real-site-sample --evaluation-count 4 --llm-extractor-provider deepseek --json-output evaluations\real-site-4-compare.json
```

- [ ] **Step 2: Update the result summary**

State the verified result plainly: baseline `3/4`, `llm-demo` `3/4`, `deepseek` `4/4`, with the filtered Anthropic sample named explicitly.

- [ ] **Step 3: Update the project story**

Describe `--real-site-sample` as a benchmark artifact that now carries explanation data, not just a smoke path.

- [ ] **Step 4: Update the work log**

Append a short note that the benchmark now explains filtered jobs in the JSON/report artifacts.

- [ ] **Step 5: Re-run docs-sensitive verification**

Run:

```powershell
pytest -q tests/test_browser.py tests/test_scaffold.py
```

Expected: pass, with no report-format regressions.

---

## Self-Review Checklist

- Spec coverage:
  - Benchmark explanation capture: Task 1.
  - JSON/report propagation: Task 2.
  - Docs and work-log alignment: Task 3.
- Placeholder scan:
  - No TBD/TODO placeholders.
  - No vague “handle edge cases” language.
- Type consistency:
  - `filtered_jobs` is the only new metadata field introduced in this plan.
  - `failure_details` remains the human-readable explanation field in evaluation artifacts.

