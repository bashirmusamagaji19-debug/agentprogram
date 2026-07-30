# Resume Portfolio Finish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 收口 Web Task Agent 为可放入 AI Agent 实习简历的、可离线复现且证据边界清晰的 portfolio 项目。

**Architecture:** 复用现有 Hybrid runtime、HITL benchmark、artifact writer 和 doctor，不新增业务 Agent 能力。新增 `--portfolio-demo` 作为薄编排入口，新增 CI 等价验证命令作为薄检查入口，README 和 interview story 作为招聘呈现层。

**Tech Stack:** Python 3.11+, argparse, existing LangGraph runtime, SQLite checkpoint, pytest/pytest-cov, Ruff, PowerShell-compatible CLI.

---

### Task 1: Add Portfolio Demo Contract

**Files:**
- Modify: `src/web_task_agent/cli.py`
- Modify: `tests/test_scaffold.py`
- Modify: `tests/test_agent_cli.py`

- [ ] **Step 1: Write failing parser and orchestration tests**

Add tests asserting `--portfolio-demo` and `--portfolio-demo-output-dir` parse, invoke the deterministic Hybrid demo and HITL benchmark, print artifact paths, and return nonzero when one stage fails.

```python
def test_portfolio_demo_parser_exposes_output_dir():
    args = build_parser().parse_args(["--portfolio-demo", "--portfolio-demo-output-dir", "demo-artifacts"])
    assert args.portfolio_demo is True
    assert args.portfolio_demo_output_dir == "demo-artifacts"


def test_portfolio_demo_writes_evidence_without_provider_keys(tmp_path, capsys):
    assert main(["--portfolio-demo", "--portfolio-demo-output-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "Portfolio demo" in output
    assert (tmp_path / "hitl-checkpoint" / "hitl-checkpoint.json").exists()
    assert (tmp_path / "hitl-checkpoint" / "hitl-checkpoint.md").exists()


def test_portfolio_demo_returns_nonzero_when_stage_fails(monkeypatch, tmp_path):
    monkeypatch.setattr("web_task_agent.cli.run_hitl_evaluation", lambda: (_ for _ in ()).throw(RuntimeError("fixture failed")))
    assert main(["--portfolio-demo", "--portfolio-demo-output-dir", str(tmp_path)]) == 1
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_cli.py -k portfolio_demo -q
```

Expected: parser attribute or command behavior is missing.

- [ ] **Step 3: Implement the thin portfolio command**

Add parser flags and a `run_portfolio_demo(args)` helper in `cli.py`. The helper must:

1. call existing doctor checks or fail clearly if the environment is invalid;
2. run the deterministic Hybrid demo into a child directory;
3. call existing `run_hitl_evaluation` and `write_hitl_evaluation_artifacts` into `hitl-checkpoint`;
4. print stage names, summary metrics, and generated paths;
5. return `0` only after all stages complete, otherwise return `1` without fabricating metrics.

Do not read provider keys or make network calls in this mode. Add the command to `--help` and `--print-demo-script` output.

- [ ] **Step 4: Run focused tests and CLI smoke**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_cli.py tests/test_scaffold.py -k portfolio_demo -q
..\..\.venv\Scripts\web-task-agent.exe --portfolio-demo --portfolio-demo-output-dir portfolio-artifacts
```

Expected: tests pass and the command prints paths for Hybrid and HITL artifacts.

- [ ] **Step 5: Commit the portfolio entry point**

```powershell
git add src/web_task_agent/cli.py tests/test_agent_cli.py tests/test_scaffold.py
git commit -m "feat: add offline portfolio demo entrypoint"
```

### Task 2: Add CI-Equivalent Release Check

**Files:**
- Modify: `src/web_task_agent/cli.py`
- Modify: `tests/test_agent_cli.py`
- Modify: `README.md`

- [ ] **Step 1: Write the release-check contract test**

Add a parser/help test for `--release-check` and a subprocess-safe test that verifies the command reports named stages and returns success when the CI-equivalent focused Ruff, pytest, coverage, wheel, doctor, strict HITL benchmark, and diff checks pass.

- [ ] **Step 2: Run the focused test and verify the command is absent**

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_cli.py -k release_check -q
```

Expected: missing parser flag or helper.

- [ ] **Step 3: Implement release-check without hiding failures**

Implement a small subprocess runner that executes the exact CI Ruff scope, deterministic pytest with coverage, wheel build into a temporary directory, doctor, strict HITL benchmark into a temporary directory, and `git diff --check`. Print `[PASS]`/`[FAIL]` per stage and return nonzero if any stage fails. Do not run full-repository Ruff because it is outside the CI gate and currently contains historical debt.

- [ ] **Step 4: Run release-check and inspect output**

```powershell
..\..\.venv\Scripts\web-task-agent.exe --release-check
```

Expected: all named CI-equivalent stages pass and the command exits `0`.

- [ ] **Step 5: Commit the release check**

```powershell
git add src/web_task_agent/cli.py tests/test_agent_cli.py README.md
git commit -m "feat: add CI-equivalent release check"
```

### Task 3: Finish Resume-Facing Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/interview-benchmark-story.md`
- Modify: `tests/test_scaffold.py`
- Create: `docs/work-log/2026-07-30-resume-portfolio-finish.md`

- [ ] **Step 1: Write documentation contract assertions**

Assert the public docs contain the portfolio command, HITL approve/reject/replay evidence, exact metric definitions, three resume bullets, 60-second story, no-GPU boundary, and explicit fixture/provider limitations.

- [ ] **Step 2: Run the documentation test and verify missing sections**

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_scaffold.py -k portfolio_docs -q
```

- [ ] **Step 3: Update the public entry points**

Put a compact project summary, architecture map, stop conditions, one-command portfolio demo, artifact links, and honest limitations near the top of README. Extend the interview story with HITL as the primary reliability narrative and keep historical provider metrics labeled with date/scope.

- [ ] **Step 4: Add the work log**

Record motivation, chosen scope, files, commits, command outputs, metrics, limitations, interview wording, and the exact stopping rule. State that no cloud server, GPU, training, API key, push, PR, merge, or worktree deletion is required for the offline portfolio path.

- [ ] **Step 5: Commit the resume-facing documentation**

```powershell
git add README.md docs/interview-benchmark-story.md tests/test_scaffold.py docs/work-log/2026-07-30-resume-portfolio-finish.md
git commit -m "docs: finalize resume portfolio story"
```

### Task 4: Final Verification And Stop

**Files:**
- No production changes expected unless a verification failure identifies a direct regression.

- [ ] **Step 1: Run the full deterministic test and coverage suite**

```powershell
..\..\.venv\Scripts\python.exe -m pytest
..\..\.venv\Scripts\python.exe -m pytest --cov=web_task_agent --cov-report=term-missing
```

- [ ] **Step 2: Run focused Ruff, wheel, doctor, portfolio demo, and release check**

```powershell
..\..\.venv\Scripts\python.exe -m ruff check src/web_task_agent/agent_*.py src/web_task_agent/search_discovery.py tests/test_agent_*.py tests/test_search_discovery.py
..\..\.venv\Scripts\web-task-agent.exe --doctor
..\..\.venv\Scripts\web-task-agent.exe --portfolio-demo --portfolio-demo-output-dir portfolio-artifacts
..\..\.venv\Scripts\web-task-agent.exe --release-check
```

- [ ] **Step 3: Check evidence hygiene**

Search generated public artifacts for `Authorization`, `Bearer`, `api_key`, resume markers, raw prompts, raw responses, and page bodies. Run `git diff --check`, inspect `git status --short`, and verify no generated runtime directories are tracked.

- [ ] **Step 4: Stop at the resume-project threshold**

Stop implementation when the tests, focused lint, portfolio demo, release check, evidence hygiene, and clean worktree all pass. Report the exact commit IDs, final metrics, remaining full-Ruff debt, and the four external Git options. Do not add new Agent features after this point.
