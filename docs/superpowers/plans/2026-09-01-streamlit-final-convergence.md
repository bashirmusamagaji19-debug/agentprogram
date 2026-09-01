# Streamlit Final Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tested Streamlit interface that runs the existing Web Task Agent locally or as a single-instance cloud demo and exposes results, diagnostics, and downloadable artifacts.

**Architecture:** Keep Streamlit as a thin presentation layer. `streamlit_runner.py` owns typed UI requests, validation, existing workflow assembly, and artifact generation; `streamlit_app.py` owns widgets and rendering. Existing browser, extractor, verifier, matcher, repository, reporter, dashboard, and action-plan modules remain authoritative.

**Tech Stack:** Python 3.11+, Pydantic, Streamlit, LangGraph workflow, SQLite, pytest, pytest-cov, Ruff.

---

### Task 1: Typed UI Request And Validation

**Files:**
- Create: `src/web_task_agent/streamlit_runner.py`
- Create: `tests/test_streamlit_runner.py`

- [ ] **Step 1: Write failing validation tests**

Add tests for skill parsing, URL parsing, uploaded text decoding, target-count bounds, mode-specific required inputs, and missing provider environment-variable names.

```python
def test_seed_url_mode_requires_http_urls():
    request = UiRunRequest(data_mode="seed_urls", seed_urls=["not-a-url"])
    with pytest.raises(UiRequestError, match="HTTP"):
        validate_ui_request(request, environ={})
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests\test_streamlit_runner.py -q`

Expected: collection fails because `web_task_agent.streamlit_runner` does not exist.

- [ ] **Step 3: Implement the request boundary**

Define `UiDataMode`, `UiRunRequest`, `UiRunResult`, `UiRequestError`, `parse_skills`, `parse_seed_urls`, `decode_resume_upload`, and `validate_ui_request`. Provider checks return only missing environment-variable names and never values.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests\test_streamlit_runner.py -q`

Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/web_task_agent/streamlit_runner.py tests/test_streamlit_runner.py
git commit -m "feat: add typed Streamlit request boundary"
```

### Task 2: Existing Workflow Runner And Artifacts

**Files:**
- Modify: `src/web_task_agent/streamlit_runner.py`
- Modify: `tests/test_streamlit_runner.py`

- [ ] **Step 1: Write failing runner tests**

Test deterministic Demo execution, unique per-run output directories, JSON/Markdown/HTML/action-plan artifacts, empty real-source diagnostics, and download allowlisting.

```python
@pytest.mark.asyncio
async def test_demo_request_returns_jobs_and_artifacts(tmp_path):
    result = await run_ui_request(UiRunRequest(keyword="AI intern"), output_root=tmp_path)
    assert result.jobs
    assert set(result.artifacts) == {"json", "report", "dashboard", "action_plan"}
```

- [ ] **Step 2: Run the tests and verify RED**

Expected: tests fail because `run_ui_request` and artifact helpers are absent.

- [ ] **Step 3: Implement minimal runner**

Build `UserProfile`, choose `FakeBrowserClient` for Demo or existing aggregator/HTTP boundaries for real modes, call `WebTaskWorkflow.run_with_langgraph`, generate existing report/dashboard/action-plan plus JSON, and return `UiRunResult` with failure diagnostics and trace.

- [ ] **Step 4: Run focused and adjacent tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_streamlit_runner.py tests\test_workflow.py tests\test_dashboard.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/web_task_agent/streamlit_runner.py tests/test_streamlit_runner.py
git commit -m "feat: run existing agent workflow from Streamlit"
```

### Task 3: Streamlit Single-Page Interface

**Files:**
- Create: `src/web_task_agent/streamlit_app.py`
- Create: `streamlit_app.py`
- Create: `tests/test_streamlit_app.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing presentation-helper tests**

Test result-row conversion, diagnostic normalization, artifact labels/MIME types, and that importing the app module does not execute a run.

- [ ] **Step 2: Run the tests and verify RED**

Expected: collection fails because the application module does not exist.

- [ ] **Step 3: Add Streamlit dependency and implement the page**

Create a wide single-page layout with a sidebar for mode/providers, a main form for resume and job criteria, guarded execution, result/diagnostic/trace/download tabs, official job links, and Session State persistence. Root `streamlit_app.py` delegates to package `main()`.

- [ ] **Step 4: Run page tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_streamlit_app.py tests\test_streamlit_runner.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml streamlit_app.py src/web_task_agent/streamlit_app.py tests/test_streamlit_app.py
git commit -m "feat: add Streamlit job agent interface"
```

### Task 4: Deployment And User Documentation

**Files:**
- Create: `.streamlit/config.toml`
- Modify: `README.md`
- Modify: `docs/project-story.md`
- Create: `docs/work-log/2026-09-01-streamlit-final-convergence.md`
- Modify: `tests/test_scaffold.py`

- [ ] **Step 1: Write failing deployment-contract tests**

Assert the root entrypoint, Streamlit dependency, headless configuration, README local command, cloud command, secret handling, and single-instance persistence warning.

- [ ] **Step 2: Run the contract tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests\test_scaffold.py -k streamlit -q`

Expected: new contract tests fail on missing deployment documentation/configuration.

- [ ] **Step 3: Add deployment configuration and docs**

Document local launch, environment-variable names, upload limits, supported modes, cloud command, and SQLite/ephemeral-file limitations. Record verification evidence separately from unverified real-provider claims.

- [ ] **Step 4: Run contract tests and verify GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests\test_scaffold.py -k streamlit -q`

Expected: all Streamlit contract tests pass.

- [ ] **Step 5: Commit**

```powershell
git add .streamlit/config.toml README.md docs/project-story.md docs/work-log/2026-09-01-streamlit-final-convergence.md tests/test_scaffold.py
git commit -m "docs: add Streamlit run and deployment guide"
```

### Task 5: Full Verification And Browser Acceptance

**Files:**
- Modify as required by discovered, test-backed defects only.
- Update: `docs/work-log/2026-09-01-streamlit-final-convergence.md`

- [ ] **Step 1: Install updated editable dependencies**

Run: `.venv\Scripts\python.exe -m pip install -e ".[dev]"`

- [ ] **Step 2: Run full tests and coverage**

Run: `.venv\Scripts\python.exe -m pytest --cov=web_task_agent --cov-report=term-missing --cov-fail-under=70 -q`

Expected: zero failures and coverage at least 70%.

- [ ] **Step 3: Run project quality gates**

Run the repository's focused Ruff scope, `web-task-agent --release-check`, package build, and `git diff --check`. Record actual results; do not claim full-repository Ruff is clean.

- [ ] **Step 4: Start Streamlit and check health**

Run: `.venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.headless true --server.port 8501`

Verify `http://localhost:8501/_stcore/health` returns `ok`.

- [ ] **Step 5: Browser-test desktop and mobile**

Use the in-app browser to submit Demo mode, inspect job results, diagnostics, trace and downloads at desktop and mobile viewports, and capture screenshots. Fix only reproduced defects with a failing test first.

- [ ] **Step 6: Final consistency audit and commit**

Confirm README metrics match fresh output, no secrets or resumes are tracked, worktree contains only intended changes, then commit the final evidence update.
