# Visual Source Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the already-completed Qwen2.5-VL training, evaluation, FastAPI service, and Docker source from the verified backup into the active `visual-web-agent` repository without overwriting newer lifecycle fixes.

**Architecture:** Extract the backup to a temporary staging directory, compare it with the active repository, and copy only the missing owned modules plus their tests and deployment files. Preserve current `browser.py`, `extractor.py`, `factory.py`, parser, and VLM integration; reconnect restored code through explicit optional dependencies and rerun offline tests before any GPU validation.

**Tech Stack:** Python 3.11+, Qwen2.5-VL, PEFT/QLoRA, Transformers, FastAPI, Docker, pytest

---

**Execution repository:** Run every task in `C:\Users\13993\Desktop\大模型学习\visual-web-agent` on an isolated worktree or feature branch. The plan file lives in `Agent` only so the umbrella project has one review surface.

### Task 1: Verify the Backup and Stage It Safely

**Files:**
- Read: `../multimodal-backup-20260728/SHA256SUMS`
- Read: `../multimodal-backup-20260728/core-backup.tar.gz`

- [ ] **Step 1: Verify the core archive hash**

Run from `C:\Users\13993\Desktop\大模型学习\multimodal-backup-20260728`:

```powershell
$actual = (Get-FileHash .\core-backup.tar.gz -Algorithm SHA256).Hash.ToLower()
$expected = (Get-Content .\core-backup.tar.gz.sha256).Split()[0].ToLower()
if ($actual -ne $expected) { throw "core backup hash mismatch" }
```

Expected: command exits 0 with no output.

- [ ] **Step 2: Extract to an explicit staging path outside both repositories**

```powershell
$stage = Join-Path $env:TEMP 'visual-web-agent-recovery-20260731'
New-Item -ItemType Directory -Force -Path $stage | Out-Null
tar -xzf .\core-backup.tar.gz -C $stage projects/visual-web-agent
Resolve-Path (Join-Path $stage 'projects\visual-web-agent')
```

Expected: resolved path is inside `%TEMP%\visual-web-agent-recovery-20260731`.

- [ ] **Step 3: Confirm the required source set exists**

```powershell
$project = Join-Path $stage 'projects\visual-web-agent'
@(
  'src\visual_web_agent\training\train_qlora.py',
  'src\visual_web_agent\evaluation\evaluate_model.py',
  'src\visual_web_agent\service\app.py',
  'tests\test_training_dataset.py',
  'tests\test_evaluation_runner.py',
  'tests\test_service.py',
  'Dockerfile'
) | ForEach-Object {
  if (-not (Test-Path (Join-Path $project $_))) { throw "missing backup file: $_" }
}
```

Expected: command exits 0.

### Task 2: Restore Missing Modules Without Replacing Active Extraction Code

**Files:**
- Create: `src/visual_web_agent/training/`
- Create: `src/visual_web_agent/evaluation/`
- Create: `src/visual_web_agent/service/`
- Create: `tests/test_training_dataset.py`
- Create: `tests/test_evaluation_metrics.py`
- Create: `tests/test_evaluation_runner.py`
- Create: `tests/test_service.py`
- Create: `Dockerfile`
- Modify: `pyproject.toml`

- [ ] **Step 1: Copy only the missing owned directories and tests**

Run from the active `visual-web-agent` repository:

```powershell
$stageProject = Join-Path $env:TEMP 'visual-web-agent-recovery-20260731\projects\visual-web-agent'
Copy-Item -Recurse -Force (Join-Path $stageProject 'src\visual_web_agent\training') .\src\visual_web_agent\
Copy-Item -Recurse -Force (Join-Path $stageProject 'src\visual_web_agent\evaluation') .\src\visual_web_agent\
Copy-Item -Recurse -Force (Join-Path $stageProject 'src\visual_web_agent\service') .\src\visual_web_agent\
Copy-Item -Force (Join-Path $stageProject 'tests\test_training_dataset.py') .\tests\
Copy-Item -Force (Join-Path $stageProject 'tests\test_evaluation_metrics.py') .\tests\
Copy-Item -Force (Join-Path $stageProject 'tests\test_evaluation_runner.py') .\tests\
Copy-Item -Force (Join-Path $stageProject 'tests\test_service.py') .\tests\
Copy-Item -Force (Join-Path $stageProject 'Dockerfile') .\Dockerfile
Get-ChildItem .\src, .\tests -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
```

Expected: only missing modules/tests and Dockerfile appear in `git status`; current extraction files remain unchanged.

- [ ] **Step 2: Prove newer lifecycle files were not overwritten**

```powershell
git diff --exit-code -- src/visual_web_agent/browser.py src/visual_web_agent/extractor.py src/visual_web_agent/factory.py src/visual_web_agent/parser.py src/visual_web_agent/vlm.py
```

Expected: exit 0 and no diff.

- [ ] **Step 3: Merge optional dependencies explicitly**

Add these optional groups to `pyproject.toml`, taking exact version lower bounds from the staged `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = [
  "pytest>=8.2",
  "pytest-asyncio>=0.23",
]
training = [
  "accelerate>=1.0",
  "datasets>=3.0",
  "peft>=0.13",
  "transformers>=4.49",
]
service = [
  "fastapi>=0.115",
  "uvicorn>=0.30",
]
```

Do not add CUDA wheels or pin a platform-specific PyTorch build to the base package.

- [ ] **Step 4: Install only offline test dependencies locally**

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,service]"
```

Expected: installation succeeds without downloading model weights.

- [ ] **Step 5: Run restored offline tests**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: all original and restored offline tests pass; no test attempts a GPU or external model download.

- [ ] **Step 6: Commit recovered source**

```powershell
git add pyproject.toml Dockerfile src/visual_web_agent/training src/visual_web_agent/evaluation src/visual_web_agent/service tests
git commit -m "feat: restore multimodal training and service source"
```

### Task 3: Restore Only Redacted Evaluation Artifacts

**Files:**
- Create: `artifacts/evaluation/base.synthetic.json`
- Create: `artifacts/evaluation/lora.synthetic.json`
- Create: `artifacts/evaluation/base-vs-lora.json`
- Create: `artifacts/evaluation/base-vs-lora.md`
- Modify: `.gitignore`

- [ ] **Step 1: Copy the four final comparison artifacts**

```powershell
$stageProject = Join-Path $env:TEMP 'visual-web-agent-recovery-20260731\projects\visual-web-agent'
New-Item -ItemType Directory -Force .\artifacts\evaluation | Out-Null
Copy-Item -Force (Join-Path $stageProject 'artifacts\evaluation\base.synthetic.json') .\artifacts\evaluation\
Copy-Item -Force (Join-Path $stageProject 'artifacts\evaluation\lora.synthetic.json') .\artifacts\evaluation\
Copy-Item -Force (Join-Path $stageProject 'artifacts\evaluation\base-vs-lora.json') .\artifacts\evaluation\
Copy-Item -Force (Join-Path $stageProject 'artifacts\evaluation\base-vs-lora.md') .\artifacts\evaluation\
```

- [ ] **Step 2: Scan for secrets and absolute cloud paths**

```powershell
rg -n -i "api[_-]?key|authorization|bearer|secret|token|/data/|ssh-rsa|BEGIN .*PRIVATE" artifacts/evaluation
```

Expected: no credential matches. `/data/` matches must be replaced with portable artifact identifiers before commit.

- [ ] **Step 3: Verify the synthetic-data boundary and zero delta**

```powershell
$result = Get-Content .\artifacts\evaluation\base-vs-lora.json -Raw | ConvertFrom-Json
if ($result.sample_count -ne 20) { throw 'unexpected sample count' }
if ($result.delta -ne 0) { throw 'unexpected remembered delta; inspect schema and actual result' }
```

Expected: sample count is 20 and delta is 0. If field names differ, inspect the JSON and write a focused schema test instead of editing the result.

- [ ] **Step 4: Keep checkpoints and raw datasets ignored**

Append:

```gitignore
checkpoints/
datasets/
artifacts/evaluation/*.raw.json
```

- [ ] **Step 5: Commit verified artifacts**

```powershell
git add .gitignore artifacts/evaluation
git commit -m "docs: restore verified multimodal evaluation evidence"
```

### Task 4: Revalidate Service Lifecycle and Busy-Gate Semantics

**Files:**
- Modify: `tests/test_service.py`
- Modify: `src/visual_web_agent/service/runtime.py`
- Modify: `src/visual_web_agent/service/app.py`

- [ ] **Step 1: Run service tests in isolation**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_service.py -q
```

Expected: tests pass. If they fail, preserve the failure output in the work log before changing code.

- [ ] **Step 2: Add explicit acceptance assertions if absent**

Ensure tests prove all of the following:

```python
assert health["ready"] is True
assert health["model"] == "Qwen/Qwen2.5-VL-3B-Instruct"
assert health["device"] in {"cpu", "cuda:0"}
assert timeout_response.status_code == 503
assert runtime.close_calls == 1
```

The model call must be faked; offline tests cannot allocate GPU memory.

- [ ] **Step 3: Make only the minimal lifecycle fix required by the failing assertion**

The service must initialize lifecycle fields in `__init__`, reject concurrent work at the busy gate, clear the gate in `finally`, and allow cleanup before startup or repeatedly. Do not change extraction semantics in this task.

- [ ] **Step 4: Run all tests and Docker build syntax validation**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
docker build --check -f Dockerfile .
git diff --check
```

Expected: all tests pass, Dockerfile check exits 0, and diff check is silent. If Docker is unavailable, record that as unverified rather than claiming the image builds.

- [ ] **Step 5: Commit lifecycle verification**

```powershell
git add src/visual_web_agent/service tests/test_service.py
git commit -m "test: verify visual service lifecycle"
```

### Task 5: Connect Real-Snapshot Evaluation Without Retraining

**Files:**
- Modify: `src/visual_web_agent/evaluation/evaluate_model.py`
- Create: `tests/test_real_snapshot_evaluation.py`
- Modify: `README.md`

- [ ] **Step 1: Add a failing test for frozen snapshot input**

The test must provide a temporary JSONL file with `snapshot_id`, `url`, image path, and ground-truth fields; invoke evaluation with a fake model; and assert that per-sample output preserves `snapshot_id`, provider, model, error, latency, and predicted fields.

- [ ] **Step 2: Run the focused test**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_real_snapshot_evaluation.py -q
```

Expected: failure because the evaluator does not yet accept frozen snapshot records.

- [ ] **Step 3: Add a frozen-snapshot adapter**

Implement a parser that validates every record and refuses missing `snapshot_id`, missing ground truth, or missing image. Reuse the current evaluation metrics; do not add new model providers.

- [ ] **Step 4: Verify same-sample comparison output**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_real_snapshot_evaluation.py tests\test_evaluation_metrics.py tests\test_evaluation_runner.py -q
```

Expected: all focused tests pass.

- [ ] **Step 5: Document the boundary**

README must state that synthetic base/LoRA results prove pipeline completion only; real-job claims require the same frozen snapshot matrix used by `Agent`.

- [ ] **Step 6: Commit real-snapshot evaluation support**

```powershell
git add src/visual_web_agent/evaluation/evaluate_model.py tests/test_real_snapshot_evaluation.py README.md
git commit -m "feat: evaluate VLM on frozen real snapshots"
```

### Task 6: Final Repository Verification

**Files:**
- Create: `docs/work-log/2026-07-31-visual-source-recovery.md`

- [ ] **Step 1: Run final offline verification**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
git diff --check
git status --short
```

Expected: tests and `pip check` pass, diff check is silent, and status contains only the work-log file before commit.

- [ ] **Step 2: Record evidence without overclaiming**

The work log must record the backup SHA-256, restored paths, current test count, Docker verification status, synthetic 20-sample boundary, base score, LoRA score, delta, and whether a real-snapshot evaluation has actually run.

- [ ] **Step 3: Commit the work log**

```powershell
git add docs/work-log/2026-07-31-visual-source-recovery.md
git commit -m "docs: record visual source recovery"
```
