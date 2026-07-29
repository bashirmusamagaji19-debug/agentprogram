# Visual Provider Quality Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the real `qwen-vl` visual provider path fail clearly when it produces no meaningful job fields, while keeping comparison reports and demo scripts useful for interview/demo validation.

**Architecture:** Keep `visual-web-agent` responsible for Playwright screenshots and Qwen-VL calls. Keep `Agent/src/web_task_agent/visual_provider.py` as the quality boundary that distinguishes VLM-call success from meaningful extraction success, and make the CLI convert provider-only zero-valid-job runs into a clear non-zero smoke result. Preserve the existing workflow, verifier, report, JSON, and comparison artifacts so failures remain diagnosable.

**Tech Stack:** Python 3.11+, pytest, argparse CLI, existing `web_task_agent` workflow/evaluation/visual provider modules, sibling `visual_web_agent` package, DashScope/Qwen-VL.

---

## File Structure

- Modify: `Agent/src/web_task_agent/cli.py`
  - Add a dedicated provider smoke failure policy after reports/JSON diagnostics are emitted.
  - Keep `--compare-llm-extractor` as a comparison command, but make provider failure visible in console output.
- Modify: `Agent/src/web_task_agent/visual_provider.py`
  - Tighten or document the meaningful-field gate if the current checks are too permissive.
- Modify: `Agent/tests/test_visual_provider.py`
  - Add adapter tests proving placeholder/empty fields return `success=False`.
- Modify: `Agent/tests/test_scaffold.py`
  - Add CLI tests proving provider-only zero-valid-job runs exit non-zero and print diagnostics.
  - Add comparison tests proving `qwen-vl: 0/1` is visible but does not leak browser resources.
- Modify: `Agent/docs/work-log/2026-06-29-visual-provider-bridge.md`
  - Record the final smoke behavior and commands.
- Modify: `Agent/README.md`
  - Clarify that provider smoke commands require real public URLs and a successful run should produce at least one valid job.

---

### Task 1: Lock Meaningful Visual Extraction Semantics

**Files:**
- Modify: `Agent/src/web_task_agent/visual_provider.py`
- Modify: `Agent/tests/test_visual_provider.py`

- [ ] **Step 1: Write failing tests for placeholder fields**

Append these tests to `Agent/tests/test_visual_provider.py`:

```python
@pytest.mark.asyncio
async def test_qwen_adapter_rejects_placeholder_visual_fields():
    class PlaceholderJob:
        title = "Unknown Title"
        company = "Unknown Company"
        location = "Unknown Location"
        requirements = ""
        responsibilities = ""
        skills = []
        confidence = 0.0

    class FakeExternalExtractor:
        async def extract(self, url: str):
            class Result:
                success = True
                job = PlaceholderJob()
                error = ""

            return Result()

    adapter = build_configured_visual_extractor(
        provider="qwen-vl",
        extractor_factory=lambda: FakeExternalExtractor(),
    )

    result = await adapter.extract(
        BrowserPage(
            url="https://example.com/jobs/visual",
            title="",
            content="",
            source="visual-provider",
        )
    )

    assert result.success is False
    assert result.fields is None
    assert "placeholder fields" in result.error


@pytest.mark.asyncio
async def test_qwen_adapter_rejects_title_only_visual_fields():
    class TitleOnlyJob:
        title = "AI Engineer"
        company = "Example Vision"
        location = "Remote"
        requirements = ""
        responsibilities = ""
        skills = []
        confidence = 0.8

    class FakeExternalExtractor:
        async def extract(self, url: str):
            class Result:
                success = True
                job = TitleOnlyJob()
                error = ""

            return Result()

    adapter = build_configured_visual_extractor(
        provider="qwen-vl",
        extractor_factory=lambda: FakeExternalExtractor(),
    )

    result = await adapter.extract(
        BrowserPage(
            url="https://example.com/jobs/visual",
            title="",
            content="",
            source="visual-provider",
        )
    )

    assert result.success is False
    assert result.fields is None
    assert "placeholder fields" in result.error
```

- [ ] **Step 2: Run tests and confirm the current behavior**

Run:

```powershell
cd C:\Users\13993\Desktop\大模型学习\Agent
.\.venv\Scripts\python.exe -m pytest tests\test_visual_provider.py::test_qwen_adapter_rejects_placeholder_visual_fields tests\test_visual_provider.py::test_qwen_adapter_rejects_title_only_visual_fields -q
```

Expected: PASS if commit `d8b47ca` already fully covers this. If either test fails, continue to Step 3.

- [ ] **Step 3: Tighten the quality gate if needed**

In `Agent/src/web_task_agent/visual_provider.py`, make `_visual_fields_are_meaningful()` require title, company, at least one body field, and positive confidence:

```python
def _visual_fields_are_meaningful(fields: VisualJobFields) -> bool:
    """Return True only when the VLM produced usable job content."""
    title = fields.title.strip()
    company = fields.company.strip()
    requirements = fields.requirements.strip()
    responsibilities = fields.responsibilities.strip()

    title_ok = bool(title) and title != "Unknown Title"
    company_ok = bool(company) and company != "Unknown Company"
    has_body = bool(requirements) or bool(responsibilities)
    confidence_ok = fields.confidence > 0.0
    return title_ok and company_ok and has_body and confidence_ok
```

Keep the adapter failure result:

```python
if not _visual_fields_are_meaningful(fields):
    return VisualExtractionResult(
        url=page.url,
        success=False,
        fields=None,
        error=(
            "visual extraction produced empty or placeholder fields "
            f"(title={fields.title[:40]!r}, company={fields.company[:30]!r}, "
            f"confidence={fields.confidence:.2f})"
        ),
    )
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_visual_provider.py -q
```

Expected: all visual provider tests pass.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src\web_task_agent\visual_provider.py tests\test_visual_provider.py
git commit -m "test: lock visual provider quality gate"
```

---

### Task 2: Make Provider Smoke Fail Clearly When It Produces No Valid Jobs

**Files:**
- Modify: `Agent/src/web_task_agent/cli.py`
- Modify: `Agent/tests/test_scaffold.py`

- [ ] **Step 1: Write a failing CLI smoke test**

Append this test to `Agent/tests/test_scaffold.py`:

```python
def test_cli_visual_provider_zero_valid_jobs_returns_nonzero(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    class EmptyProviderAdapter:
        provider = "qwen-vl"
        model = "qwen-vl-plus"
        uses_own_browser = True
        closed = False

        async def extract(self, page):
            from web_task_agent.visual_extractor import VisualExtractionResult

            return VisualExtractionResult(
                url=page.url,
                success=False,
                fields=None,
                error="visual extraction produced empty or placeholder fields",
            )

        async def close(self):
            self.closed = True

    adapter = EmptyProviderAdapter()
    monkeypatch.setattr(
        "web_task_agent.cli.build_configured_visual_extractor",
        lambda *, provider, model=None: adapter,
    )

    exit_code = main(
        [
            "--seed-url",
            "https://example.com/jobs/visual-ai-intern",
            "--target-count",
            "1",
            "--visual-extractor-provider",
            "qwen-vl",
            "--json-output",
            "outputs/visual-provider-empty.json",
        ]
    )

    assert exit_code == 2
    assert adapter.closed is True
    captured = capsys.readouterr()
    assert "Visual extractor provider: qwen-vl" in captured.out
    assert "Valid jobs: 0" in captured.out
    assert "Visual provider diagnostics:" in captured.out
    assert "produced no valid jobs" in captured.out
    assert (tmp_path / "outputs" / "visual-provider-empty.json").exists()
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_visual_provider_zero_valid_jobs_returns_nonzero -q
```

Expected: FAIL because the current CLI prints diagnostics but still returns `0`.

- [ ] **Step 3: Implement provider smoke failure policy**

In `Agent/src/web_task_agent/cli.py`, after JSON/report/dashboard/action-plan artifacts have been written, return `2` for provider-only runs with zero valid jobs.

Use this exact helper:

```python
def _visual_provider_run_failed(args: argparse.Namespace, valid_jobs: int) -> bool:
    """Return True when a real provider smoke run should fail the CLI."""
    return (
        bool(args.visual_extractor_provider)
        and not args.compare_llm_extractor
        and not args.evaluate
        and valid_jobs == 0
    )
```

At the end of the normal workflow branch, immediately before `return 0`, add:

```python
if _visual_provider_run_failed(args, valid_jobs):
    print(
        "Visual provider produced no valid jobs. "
        "Treating this provider smoke run as failed; inspect diagnostics and JSON output."
    )
    return 2
return 0
```

Do not place this before `write_json_output()`, dashboard generation, or action-plan/report updates; the failure must still leave artifacts for diagnosis.

- [ ] **Step 4: Run the focused CLI smoke test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_visual_provider_zero_valid_jobs_returns_nonzero -q
```

Expected: PASS.

- [ ] **Step 5: Run adjacent CLI tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_seed_url_can_use_visual_extractor_provider tests\test_scaffold.py::test_cli_seed_url_can_use_visual_extractor_demo tests\test_scaffold.py::test_cli_demo_and_visual_provider_are_mutually_exclusive -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add src\web_task_agent\cli.py tests\test_scaffold.py
git commit -m "fix: fail provider smoke when no valid jobs"
```

---

### Task 3: Keep Comparison Diagnostic But Non-Blocking

**Files:**
- Modify: `Agent/src/web_task_agent/cli.py`
- Modify: `Agent/tests/test_scaffold.py`

- [ ] **Step 1: Write a comparison test for failed provider row**

Append this test to `Agent/tests/test_scaffold.py`:

```python
def test_cli_compare_visual_provider_zero_valid_jobs_stays_comparison_success(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    class EmptyProviderAdapter:
        provider = "qwen-vl"
        model = "qwen-vl-plus"
        uses_own_browser = True
        closed = False

        async def extract(self, page):
            from web_task_agent.visual_extractor import VisualExtractionResult

            return VisualExtractionResult(
                url=page.url,
                success=False,
                fields=None,
                error="visual extraction produced empty or placeholder fields",
            )

        async def close(self):
            self.closed = True

    adapter = EmptyProviderAdapter()
    monkeypatch.setattr(
        "web_task_agent.cli.build_configured_visual_extractor",
        lambda *, provider, model=None: adapter,
    )

    exit_code = main(
        [
            "--compare-llm-extractor",
            "--seed-url",
            "https://example.com/jobs/visual-ai-intern",
            "--visual-extractor-provider",
            "qwen-vl",
            "--json-output",
            "evaluations/visual-provider-empty-comparison.json",
        ]
    )

    assert exit_code == 0
    assert adapter.closed is True
    captured = capsys.readouterr()
    assert "qwen-vl: 0/1" in captured.out
    assert "Comparison JSON written to:" in captured.out
    payload = json.loads(
        (tmp_path / "evaluations" / "visual-provider-empty-comparison.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["extractors"]["qwen-vl"]["completed_tasks"] == 0
    assert payload["extractors"]["qwen-vl"]["failure_counts"]["verification_filtered"] == 1
```

- [ ] **Step 2: Run the comparison test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_compare_visual_provider_zero_valid_jobs_stays_comparison_success -q
```

Expected: PASS if the existing `finally close()` and comparison semantics are correct. If it fails because the row or close behavior is missing, continue to Step 3.

- [ ] **Step 3: Ensure provider comparison closes the adapter**

In `Agent/src/web_task_agent/cli.py`, keep this shape inside `run_llm_extractor_comparison()`:

```python
if args.visual_extractor_provider:
    try:
        provider = build_cli_visual_extractor(args)
    except VisualProviderConfigurationError as exc:
        print(f"Visual extractor is not configured: {exc}")
        raise
    try:
        provider_eval = await EvaluationRunner(
            args.evaluation_dir,
            browser_factory=browser_factory,
            extractor_factory=lambda task: PageExtractor(),
            visual_extractor_factory=lambda task: provider,
        ).run(tasks=tasks)
        extractors[args.visual_extractor_provider] = provider_eval.model_dump(mode="json")
    finally:
        if hasattr(provider, "close"):
            await provider.close()
```

- [ ] **Step 4: Run comparison-related tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_compare_extractor_can_include_visual_provider tests\test_scaffold.py::test_cli_compare_visual_provider_zero_valid_jobs_stays_comparison_success -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src\web_task_agent\cli.py tests\test_scaffold.py
git commit -m "test: cover failed visual provider comparison row"
```

---

### Task 4: Refresh Docs and Work Log for Final Provider Semantics

**Files:**
- Modify: `Agent/README.md`
- Modify: `Agent/docs/work-log/2026-06-29-visual-provider-bridge.md`

- [ ] **Step 1: Update README provider notes**

In `Agent/README.md`, update the real visual provider section to say:

```markdown
### Real visual provider

The real Qwen-VL visual provider uses the sibling `visual-web-agent` package for Playwright screenshots and VLM extraction.

Install the sibling package in the Agent virtualenv:

```powershell
.\.venv\Scripts\python.exe -m pip install -e "..\visual-web-agent"
```

Run the deterministic visual fixture path when you need a stable local demo:

```powershell
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/visual-ai-intern" --demo --target-count 1 --visual-extractor-demo --json-output outputs\visual-demo.json
```

Run the real provider only against public, reachable job pages:

```powershell
.\.venv\Scripts\web-task-agent.exe --seed-url "https://job-boards.greenhouse.io/anthropic/jobs/5116927008" --target-count 1 --visual-extractor-provider qwen-vl --json-output outputs\visual-provider.json
```

For provider smoke runs, `Valid jobs: 0` returns exit code `2` after writing diagnostics and JSON output. This prevents an empty visual extraction from looking like a successful provider validation.
```

- [ ] **Step 2: Update the work log**

Append to `Agent/docs/work-log/2026-06-29-visual-provider-bridge.md`:

```markdown
## Final provider quality gate

- Real provider extraction now distinguishes a successful VLM call from meaningful job extraction.
- Placeholder output such as `Unknown Title`, `Unknown Company`, empty body fields, or zero confidence is treated as visual extraction failure.
- Provider smoke runs with `--visual-extractor-provider qwen-vl` return exit code `2` when they produce `Valid jobs: 0`, after writing the report and JSON diagnostics.
- Comparison runs remain exit code `0` because their purpose is side-by-side measurement; the failed provider row is visible as `qwen-vl: 0/1`.
```

- [ ] **Step 3: Verify demo script text**

Run:

```powershell
.\.venv\Scripts\web-task-agent.exe --print-demo-script
```

Expected:

```text
Demo script
...
--visual-extractor-demo
...
--visual-extractor-provider qwen-vl
```

The provider command must not include `--demo`.

- [ ] **Step 4: Commit**

Run:

```powershell
git add README.md docs\work-log\2026-06-29-visual-provider-bridge.md
git commit -m "docs: clarify visual provider smoke semantics"
```

---

### Task 5: Final Verification

**Files:**
- No new implementation files.

- [ ] **Step 1: Run full automated tests**

Run from `Agent`:

```powershell
cd C:\Users\13993\Desktop\大模型学习\Agent
.\.venv\Scripts\python.exe -m pytest
```

Expected:

```text
passed
```

Run from `visual-web-agent`:

```powershell
cd C:\Users\13993\Desktop\大模型学习\visual-web-agent
.\.venv\Scripts\python.exe -m pytest
```

Expected:

```text
passed
```

- [ ] **Step 2: Re-run the old failing provider smoke**

Run from `Agent`:

```powershell
cd C:\Users\13993\Desktop\大模型学习\Agent
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/visual-ai-intern" --target-count 1 --visual-extractor-provider qwen-vl --json-output outputs\visual-provider-review.json
```

Expected if Qwen-VL still produces placeholder fields for `example.com`:

```text
Visual extractor provider: qwen-vl
Valid jobs: 0
Visual provider diagnostics:
Visual provider produced no valid jobs.
```

Expected exit code: `2`.

- [ ] **Step 3: Re-run comparison smoke**

Run:

```powershell
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --seed-url "https://example.com/jobs/visual-ai-intern" --visual-extractor-provider qwen-vl --json-output evaluations\visual-provider-review.json
```

Expected:

```text
LLM extractor comparison
baseline: 1/1
llm-demo: 1/1
qwen-vl: 0/1
Comparison report written to:
Comparison JSON written to:
```

There must be no Windows `unclosed transport` / `_ProactorBasePipeTransport.__del__` warnings.

- [ ] **Step 4: Run real public URL provider smoke**

Run:

```powershell
.\.venv\Scripts\web-task-agent.exe --seed-url "https://job-boards.greenhouse.io/anthropic/jobs/5116927008" --target-count 1 --visual-extractor-provider qwen-vl --json-output outputs\visual-provider-real-url.json
```

Expected success path if the page and API are reachable:

```text
Visual extractor provider: qwen-vl
Valid jobs: 1
JSON output written to:
```

Expected failure path if the live page/API changes:

```text
Visual provider diagnostics:
Visual provider produced no valid jobs.
```

Exit code must match the result: `0` for at least one valid job, `2` for zero valid jobs.

- [ ] **Step 5: Check generated JSON**

Run:

```powershell
Get-Content -Raw outputs\visual-provider-review.json | ConvertFrom-Json | Select-Object -ExpandProperty metadata
Get-Content -Raw evaluations\visual-provider-review.json | ConvertFrom-Json | Select-Object -ExpandProperty extractors
```

Expected:

```text
extractor_mode : visual-provider
visual_provider : qwen-vl
visual_extraction : @{successes=0; failures=1; ...}
```

For comparison JSON, `extractors.qwen-vl.completed_tasks` should reflect the actual provider outcome.

- [ ] **Step 6: Check git status**

Run:

```powershell
git status --short
```

Expected: clean worktree after commits.

---

## Self-Review

- Spec coverage: This plan covers the remaining验收 gap: provider placeholder output must not look like successful extraction, provider-only smoke must fail non-zero when `Valid jobs: 0`, comparison remains diagnostic, and docs explain the behavior.
- Placeholder scan: No `TBD`, `TODO`, or unspecified “add tests” steps remain.
- Type consistency: All snippets use existing names from the current repo: `VisualExtractionResult`, `BrowserPage`, `build_configured_visual_extractor`, `main`, `visual_extraction`, and `qwen-vl`.
