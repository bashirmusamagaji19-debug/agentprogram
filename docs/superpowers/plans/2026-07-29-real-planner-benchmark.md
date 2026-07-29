# Real Planner Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compare deterministic, DeepSeek, and Qwen planners over the same five controlled Hybrid Agent runtime scenarios and publish auditable JSON/Markdown evidence.

**Architecture:** Add provider-call telemetry to the existing OpenAI-compatible planner, then introduce a focused planner benchmark module that owns scenario construction, matrix execution, aggregation, and rendering. Add a narrow CLI mode that parses providers, runs the matrix, writes artifacts, and reports skipped providers without exposing secrets.

**Tech Stack:** Python 3.11, Pydantic v2, LangGraph, pytest/pytest-asyncio, Ruff, existing provider transport and Hybrid Agent runtime.

---

### Task 1: Planner Call Telemetry

**Files:**
- Modify: `src/web_task_agent/agent_planner.py`
- Test: `tests/test_agent_planner.py`

- [ ] **Step 1: Write failing telemetry tests**

Add tests that return OpenAI-compatible `usage` data and assert `planner.telemetry` records calls, successful/failed calls, latency, and token totals. Add a second transport that raises `TimeoutError` and assert the failure is counted without storing request or response content.

```python
assert planner.telemetry.calls == 1
assert planner.telemetry.successful_calls == 1
assert planner.telemetry.total_tokens == 15
assert planner.telemetry.total_latency_ms >= 0

with pytest.raises(TimeoutError):
    await failing_planner.decide(_state())
assert failing_planner.telemetry.failed_calls == 1
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_agent_planner.py -q`

Expected: failure because `OpenAiCompatibleAgentPlanner` has no `telemetry` attribute.

- [ ] **Step 3: Implement minimal telemetry**

Add a Pydantic `PlannerTelemetry` model with counters for calls, successful calls, failed calls, latency, and prompt/completion/total tokens. Wrap transport and parsing in `perf_counter()` timing; update counters in `try/except/finally`; read only numeric values from `response["usage"]`.

```python
class PlannerTelemetry(BaseModel):
    calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    total_latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
```

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/test_agent_planner.py -q`

Expected: all planner tests pass.

Commit: `feat: record planner call telemetry`

### Task 2: Benchmark Types, Parsing, And Aggregation

**Files:**
- Create: `src/web_task_agent/agent_planner_benchmark.py`
- Create: `tests/test_agent_planner_benchmark.py`

- [ ] **Step 1: Write failing model and parser tests**

Test provider parsing defaults to all three providers, deduplicates input, and rejects unsupported names. Construct two `DecisionAgentState` objects and assert aggregation separates task completion from loop termination and computes fallback, invalid-action, step, latency, and token metrics.

```python
assert parse_planner_benchmark_providers(None) == ["deterministic", "deepseek", "qwen"]
with pytest.raises(ValueError, match="Unsupported planner benchmark provider"):
    parse_planner_benchmark_providers("unknown")
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_agent_planner_benchmark.py -q`

Expected: collection fails because the module does not exist.

- [ ] **Step 3: Implement typed result models and aggregation**

Create `PlannerBenchmarkCaseResult`, `PlannerBenchmarkProviderResult`, and `PlannerBenchmarkMatrix` Pydantic models. Implement provider parsing and `summarize_planner_provider()` using final runtime states and optional `PlannerTelemetry`.

```python
SUPPORTED_PLANNER_BENCHMARK_PROVIDERS = ("deterministic", "deepseek", "qwen")

def parse_planner_benchmark_providers(raw: str | None) -> list[str]:
    requested = raw.split(",") if raw else list(SUPPORTED_PLANNER_BENCHMARK_PROVIDERS)
    providers = list(dict.fromkeys(item.strip().lower() for item in requested if item.strip()))
    unsupported = [item for item in providers if item not in SUPPORTED_PLANNER_BENCHMARK_PROVIDERS]
    if unsupported:
        raise ValueError(f"Unsupported planner benchmark provider: {unsupported[0]}")
    return providers

def summarize_planner_provider(
    *, provider: str, model: str, case_results: list[PlannerBenchmarkCaseResult],
    telemetry: PlannerTelemetry | None = None,
) -> PlannerBenchmarkProviderResult:
    return PlannerBenchmarkProviderResult.from_cases(
        provider=provider,
        model=model,
        cases=case_results,
        telemetry=telemetry or PlannerTelemetry(),
    )
```

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/test_agent_planner_benchmark.py -q`

Expected: parser and aggregation tests pass.

Commit: `feat: model planner benchmark results`

### Task 3: Controlled Runtime Scenario Matrix

**Files:**
- Modify: `src/web_task_agent/agent_planner_benchmark.py`
- Modify: `tests/test_agent_planner_benchmark.py`

- [ ] **Step 1: Write failing scenario and matrix tests**

Assert the catalog contains exactly `seed-happy-path`, `search-happy-path`, `open-recovery`, `verifier-recovery`, and `budget-exhaustion`. Inject a recording planner factory and assert every selected provider receives all five cases with fresh state. Assert missing provider configuration yields `skipped`, while deterministic always executes.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_agent_planner_benchmark.py -q`

Expected: failures because catalog and runner functions are absent.

- [ ] **Step 3: Implement replayable scenarios and runner**

Create immutable scenario specs containing user input, pages, max steps, and optional browser failure behavior. Build a fresh `WebTaskWorkflow`, repository under a temporary run directory, and Hybrid runtime for each provider-case pair. Run cases sequentially to keep provider telemetry deterministic.

```python
async def run_planner_benchmark(
    *, providers: list[str], output_dir: Path,
    planner_factory: Callable[[str], AgentPlanner | None],
) -> PlannerBenchmarkMatrix:
    results = []
    for provider in providers:
        results.append(
            await _run_provider_cases(
                provider=provider,
                scenarios=build_planner_benchmark_scenarios(),
                output_dir=output_dir,
                planner_factory=planner_factory,
            )
        )
    return PlannerBenchmarkMatrix(providers=results)
```

The recovery browser raises `TimeoutError` for the broken URL, and the verifier-recovery first page intentionally lacks AI relevance. Provider setup exceptions create a skipped provider row without aborting other rows.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/test_agent_planner_benchmark.py -q`

Expected: all scenario and matrix tests pass.

Commit: `feat: execute controlled planner benchmark`

### Task 4: JSON And Chinese Markdown Artifacts

**Files:**
- Modify: `src/web_task_agent/agent_planner_benchmark.py`
- Modify: `tests/test_agent_planner_benchmark.py`

- [ ] **Step 1: Write failing artifact tests**

Assert Markdown contains the controlled-fixture boundary, provider matrix, completion versus termination distinction, token note, failure details, and interview wording. Assert JSON contains the version, scope, status, model, metrics, and per-case terminal reasons but not authorization headers, API keys, prompts, response content, resume text, or raw page bodies.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_agent_planner_benchmark.py -q`

Expected: failures because render/write functions are absent.

- [ ] **Step 3: Implement rendering and artifact writing**

Implement:

```python
def render_planner_benchmark_markdown(matrix: PlannerBenchmarkMatrix) -> str:
    lines = ["# Real Planner Benchmark", "", CONTROLLED_FIXTURE_SCOPE]
    lines.extend(_render_provider_matrix(matrix.providers))
    lines.extend(_render_case_details(matrix.providers))
    return "\n".join(lines) + "\n"

def write_planner_benchmark_artifacts(
    matrix: PlannerBenchmarkMatrix, output_dir: str | Path
) -> dict[str, Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "planner-benchmark.json"
    markdown_path = destination / "planner-benchmark.md"
    json_path.write_text(
        json.dumps(matrix.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(render_planner_benchmark_markdown(matrix), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}
```

Write UTF-8 `planner-benchmark.json` via `model_dump(mode="json")` and `planner-benchmark.md` via the deterministic renderer.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/test_agent_planner_benchmark.py -q`

Expected: all benchmark tests pass.

Commit: `feat: publish planner benchmark artifacts`

### Task 5: CLI Integration

**Files:**
- Modify: `src/web_task_agent/cli.py`
- Modify: `tests/test_agent_cli.py`
- Modify: `tests/test_scaffold.py`

- [ ] **Step 1: Write failing parser and routing tests**

Assert the parser accepts `--agent-planner-benchmark`, `--agent-planner-benchmark-providers`, and `--agent-planner-benchmark-output-dir`. Monkeypatch the benchmark runner and assert CLI prints provider status, artifact paths, and returns zero when at least one provider executes.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_agent_cli.py tests/test_scaffold.py -q`

Expected: failures because the flags and routing do not exist.

- [ ] **Step 3: Implement minimal CLI routing**

Import benchmark helpers, add the three flags, and route benchmark mode before normal workflow construction. Build real planners with `build_configured_agent_planner`; convert provider configuration errors to skipped rows inside the benchmark module.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/test_agent_cli.py tests/test_scaffold.py -q`

Expected: all CLI tests pass.

Commit: `feat: expose planner benchmark CLI`

### Task 6: Documentation And Real Provider Evidence

**Files:**
- Modify: `README.md`
- Modify: `docs/project-story.md`
- Create: `docs/work-log/2026-07-29-real-planner-benchmark.md`
- Generate: `docs/results/planner-benchmark/planner-benchmark.json`
- Generate: `docs/results/planner-benchmark/planner-benchmark.md`

- [ ] **Step 1: Run deterministic benchmark**

Run:

```powershell
python -m web_task_agent.cli --agent-planner-benchmark `
  --agent-planner-benchmark-providers deterministic `
  --agent-planner-benchmark-output-dir docs/results/planner-benchmark
```

Expected: deterministic provider status is `executed` and both artifacts exist.

- [ ] **Step 2: Run real DeepSeek and Qwen benchmark**

Run the same command with `deterministic,deepseek,qwen`. Accept only provider rows with populated runtime metrics; preserve provider errors honestly if a model or account is unavailable.

- [ ] **Step 3: Scan evidence for secrets**

Search generated artifacts for API-key-like strings, `Authorization`, `Bearer`, prompt/response bodies, and resume markers. Expected: no matches.

- [ ] **Step 4: Update interview documentation**

Document the command, exact live results, controlled-fixture boundary, cost/token interpretation, and one concise resume bullet. Do not claim live-site generalization.

- [ ] **Step 5: Commit evidence**

Commit: `docs: add real planner benchmark evidence`

### Task 7: Full Verification And Delivery

**Files:**
- Verify all changed files

- [ ] **Step 1: Run quality gates**

Run:

```powershell
python -m ruff check .
python -m pytest -q
python -m pytest --cov=web_task_agent --cov-report=term-missing --cov-fail-under=70 -q
git diff --check
```

Expected: Ruff passes, all tests pass, coverage is at least 70%, and diff check is clean.

- [ ] **Step 2: Review repository state and claims**

Verify every metric in README and project story matches the generated JSON. Verify no untracked secret or temporary database file is staged.

- [ ] **Step 3: Push and open PR**

Push `feature/planner-benchmark`, create a PR to `master`, wait for CI, and report the authoritative Linux test/coverage result. Do not merge without explicit user approval.
