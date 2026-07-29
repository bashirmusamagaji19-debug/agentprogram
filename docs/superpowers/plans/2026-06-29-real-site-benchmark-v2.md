# Real Site Benchmark V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real-site benchmark v2 layer that turns the current small real URL sample into an interview-ready provider matrix with site metadata, failure categories, JSON/Markdown/HTML artifacts, and repeatable smoke commands.

**Architecture:** Keep the existing `EvaluationRunner` and `WebTaskWorkflow` as the execution engine. Add a narrow benchmark layer that owns sample metadata, provider matrix orchestration, and benchmark-specific reports; it should call existing evaluation/comparison components instead of duplicating extraction, verification, or matching logic. The benchmark must remain robust to live website drift by recording URL/site metadata and failure categories rather than assuming all public pages stay stable.

**Tech Stack:** Python 3.11+, Pydantic, pytest, existing `web_task_agent.evaluation`, existing CLI provider builders, Markdown/JSON/HTML artifacts, optional DeepSeek/Qwen/Qwen-VL providers through existing environment-variable configuration.

---

## File Structure

- Create: `Agent/src/web_task_agent/benchmark.py`
  - Owns benchmark case metadata, provider result summaries, matrix execution helpers, Markdown report rendering, and JSON payload shape.
- Modify: `Agent/src/web_task_agent/cli.py`
  - Adds `--benchmark-v2`, `--benchmark-providers`, `--benchmark-limit`, and `--benchmark-dashboard`.
  - Routes benchmark runs to the new benchmark module.
  - Adds a benchmark command to `--print-demo-script`.
- Modify: `Agent/src/web_task_agent/dashboard.py`
  - Adds `render_benchmark_summary()` for the benchmark matrix HTML artifact.
- Modify: `Agent/tests/test_benchmark.py`
  - New focused tests for catalog metadata, provider parsing, matrix summary, and Markdown rendering.
- Modify: `Agent/tests/test_scaffold.py`
  - CLI smoke tests for `--benchmark-v2` using fake provider/evaluation behavior.
- Modify: `Agent/README.md`
  - Documents benchmark v2 commands and how to interpret provider matrix output.
- Modify: `Agent/docs/interview-benchmark-story.md`
  - Updates the story from one-off comparison to repeatable benchmark methodology.
- Create: `Agent/docs/work-log/2026-06-29-real-site-benchmark-v2.md`
  - Records scope, commands, expected outputs, and remaining live-site caveats.

---

### Task 1: Add Real Site Benchmark Catalog

**Files:**
- Create: `Agent/src/web_task_agent/benchmark.py`
- Create/Modify: `Agent/tests/test_benchmark.py`

- [ ] **Step 1: Write failing catalog tests**

Create `Agent/tests/test_benchmark.py`:

```python
from web_task_agent.benchmark import (
    BenchmarkCase,
    build_real_site_benchmark_v2_cases,
    parse_benchmark_providers,
)


def test_real_site_benchmark_v2_catalog_has_metadata_and_tasks():
    cases = build_real_site_benchmark_v2_cases()

    assert len(cases) >= 8
    assert {case.ats for case in cases} >= {"greenhouse"}
    assert {case.company for case in cases} >= {"Anthropic", "ScaleAI", "Reddit", "Discord"}
    assert all(case.case_id for case in cases)
    assert all(case.url.startswith("https://") for case in cases)

    task = cases[0].to_evaluation_task()
    assert task.seed_urls == [cases[0].url]
    assert task.keyword == cases[0].keyword
    assert task.location == cases[0].location
    assert task.skills == cases[0].skills


def test_benchmark_case_rejects_missing_url():
    try:
        BenchmarkCase(
            case_id="bad",
            company="Example",
            ats="greenhouse",
            role_family="ai",
            keyword="AI Engineer",
            location="Remote",
            skills=["Python"],
            url="",
            expected_signal="AI role",
        )
    except ValueError as exc:
        assert "url" in str(exc)
    else:
        raise AssertionError("BenchmarkCase should reject empty url")


def test_parse_benchmark_providers_defaults_and_dedupes():
    assert parse_benchmark_providers("") == ["baseline", "llm-demo"]
    assert parse_benchmark_providers("baseline,llm-demo,baseline,deepseek") == [
        "baseline",
        "llm-demo",
        "deepseek",
    ]
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```powershell
cd C:\Users\13993\Desktop\大模型学习\Agent
.\.venv\Scripts\python.exe -m pytest tests\test_benchmark.py -q
```

Expected: FAIL because `web_task_agent.benchmark` does not exist.

- [ ] **Step 3: Implement catalog and provider parsing**

Create `Agent/src/web_task_agent/benchmark.py`:

```python
from __future__ import annotations

from pathlib import Path
from time import perf_counter

from pydantic import BaseModel, Field, field_validator

from web_task_agent.evaluation import EvaluationResult, EvaluationTask


SUPPORTED_BENCHMARK_PROVIDERS = {
    "baseline",
    "llm-demo",
    "deepseek",
    "qwen",
    "qwen-vl",
}


class BenchmarkCase(BaseModel):
    case_id: str
    company: str
    ats: str
    role_family: str
    keyword: str
    location: str = "Remote"
    skills: list[str] = Field(default_factory=list)
    url: str
    expected_signal: str
    notes: str = ""

    @field_validator("url")
    @classmethod
    def _url_must_be_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("url must be an https URL")
        return value

    def to_evaluation_task(self) -> EvaluationTask:
        return EvaluationTask(
            keyword=self.keyword,
            location=self.location,
            target_count=1,
            skills=self.skills,
            seed_urls=[self.url],
        )


def build_real_site_benchmark_v2_cases() -> list[BenchmarkCase]:
    """Return the current real-site benchmark catalog.

    These URLs are live external pages and may drift. The benchmark records
    failure categories so a dead or changed page becomes data instead of a
    silent test assumption.
    """
    return [
        BenchmarkCase(
            case_id="anthropic-claude-evangelist",
            company="Anthropic",
            ats="greenhouse",
            role_family="ai-applications",
            keyword="Applied AI Claude Evangelist",
            location="San Francisco, CA",
            skills=["AI", "demos", "customer"],
            url="https://job-boards.greenhouse.io/anthropic/jobs/5116927008",
            expected_signal="AI application and Claude product role",
        ),
        BenchmarkCase(
            case_id="anthropic-api-platform-tpm",
            company="Anthropic",
            ats="greenhouse",
            role_family="platform",
            keyword="Technical Program Manager, API Platform",
            location="San Francisco, CA",
            skills=["API", "platform", "program management"],
            url="https://job-boards.greenhouse.io/anthropic/jobs/5256303008",
            expected_signal="API platform role",
        ),
        BenchmarkCase(
            case_id="scale-ai-builder-intern",
            company="ScaleAI",
            ats="greenhouse",
            role_family="ai-internship",
            keyword="AI Builder Intern",
            location="San Francisco, CA; New York, NY",
            skills=["AI", "Python", "intern"],
            url="https://job-boards.greenhouse.io/scaleai/jobs/4703343005",
            expected_signal="AI internship role",
        ),
        BenchmarkCase(
            case_id="scale-ai-deployment-strategist",
            company="ScaleAI",
            ats="greenhouse",
            role_family="deployment",
            keyword="AI Deployment Strategist",
            location="San Francisco, CA; New York, NY",
            skills=["AI", "strategy", "deployment"],
            url="https://job-boards.greenhouse.io/scaleai/jobs/4699458005",
            expected_signal="AI deployment role",
        ),
        BenchmarkCase(
            case_id="scale-ai-strategy-consultant",
            company="ScaleAI",
            ats="greenhouse",
            role_family="strategy",
            keyword="AI Strategy Consultant",
            location="San Francisco, CA",
            skills=["AI", "consulting", "strategy"],
            url="https://job-boards.greenhouse.io/scaleai/jobs/4472223005",
            expected_signal="AI strategy role",
        ),
        BenchmarkCase(
            case_id="reddit-analytics-engineer-us",
            company="Reddit",
            ats="greenhouse",
            role_family="analytics",
            keyword="Analytics Engineer",
            location="Remote - United States",
            skills=["SQL", "Python", "analytics"],
            url="https://job-boards.greenhouse.io/reddit/jobs/7958354",
            expected_signal="analytics engineering role",
        ),
        BenchmarkCase(
            case_id="reddit-analytics-engineer-toronto",
            company="Reddit",
            ats="greenhouse",
            role_family="analytics",
            keyword="Analytics Engineer Toronto",
            location="Toronto, Canada",
            skills=["SQL", "Python", "analytics"],
            url="https://job-boards.greenhouse.io/reddit/jobs/7958385",
            expected_signal="analytics engineering role",
        ),
        BenchmarkCase(
            case_id="discord-developer-solutions",
            company="Discord",
            ats="greenhouse",
            role_family="developer-platform",
            keyword="Director Developer Solutions",
            location="San Francisco Bay Area",
            skills=["developer relations", "platform", "leadership"],
            url="https://job-boards.greenhouse.io/discord/jobs/8480100002",
            expected_signal="developer platform leadership role",
        ),
    ]


def parse_benchmark_providers(raw: str | None) -> list[str]:
    if not raw:
        return ["baseline", "llm-demo"]
    providers: list[str] = []
    for item in raw.split(","):
        provider = item.strip()
        if not provider:
            continue
        if provider not in SUPPORTED_BENCHMARK_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_BENCHMARK_PROVIDERS))
            raise ValueError(f"Unsupported benchmark provider: {provider}. Supported: {supported}")
        if provider not in providers:
            providers.append(provider)
    return providers or ["baseline", "llm-demo"]
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_benchmark.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src\web_task_agent\benchmark.py tests\test_benchmark.py
git commit -m "feat: add real site benchmark catalog"
```

---

### Task 2: Add Benchmark Matrix Result and Report Rendering

**Files:**
- Modify: `Agent/src/web_task_agent/benchmark.py`
- Modify: `Agent/tests/test_benchmark.py`

- [ ] **Step 1: Write failing matrix/report tests**

Append to `Agent/tests/test_benchmark.py`:

```python
from web_task_agent.benchmark import (
    BenchmarkMatrixResult,
    BenchmarkProviderResult,
    render_benchmark_markdown,
)
from web_task_agent.evaluation import EvaluationResult, TaskEvaluationResult


def _fake_eval(*, completed: int, total: int, failures: dict[str, int]) -> EvaluationResult:
    return EvaluationResult(
        total_tasks=total,
        completed_tasks=completed,
        success_rate=round(completed / total, 2),
        total_valid_jobs=completed,
        average_pages_visited=1.0,
        failure_counts=failures,
        task_results=[
            TaskEvaluationResult(
                keyword="AI Builder Intern",
                location="Remote",
                pages_visited=1,
                valid_jobs=1 if completed else 0,
                success=bool(completed),
                failure_category="" if completed else "verification_filtered",
                failure_reason="" if completed else "no valid jobs",
                failure_details="" if completed else "confidence below 0.5",
            )
        ],
    )


def test_benchmark_provider_result_from_evaluation():
    provider = BenchmarkProviderResult.from_evaluation(
        provider="deepseek",
        result=_fake_eval(completed=1, total=1, failures={}),
        elapsed_seconds=2.5,
    )

    assert provider.provider == "deepseek"
    assert provider.completed_tasks == 1
    assert provider.total_tasks == 1
    assert provider.success_rate == 1.0
    assert provider.elapsed_seconds == 2.5


def test_render_benchmark_markdown_contains_matrix_and_failures():
    cases = build_real_site_benchmark_v2_cases()[:1]
    result = BenchmarkMatrixResult(
        cases=cases,
        providers=[
            BenchmarkProviderResult.from_evaluation(
                provider="baseline",
                result=_fake_eval(completed=0, total=1, failures={"verification_filtered": 1}),
                elapsed_seconds=0.1,
            ),
            BenchmarkProviderResult.from_evaluation(
                provider="deepseek",
                result=_fake_eval(completed=1, total=1, failures={}),
                elapsed_seconds=0.2,
            ),
        ],
    )

    markdown = render_benchmark_markdown(result)

    assert "# Real Site Benchmark V2" in markdown
    assert "| baseline | 0/1 | 0.00 | 0 | verification_filtered=1 |" in markdown
    assert "| deepseek | 1/1 | 1.00 | 1 | - |" in markdown
    assert "anthropic-claude-evangelist" in markdown
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_benchmark.py -q
```

Expected: FAIL because result/rendering classes do not exist.

- [ ] **Step 3: Implement matrix result and Markdown rendering**

Append to `Agent/src/web_task_agent/benchmark.py`:

```python
class BenchmarkProviderResult(BaseModel):
    provider: str
    total_tasks: int
    completed_tasks: int
    success_rate: float
    total_valid_jobs: int
    average_pages_visited: float
    failure_counts: dict[str, int] = Field(default_factory=dict)
    elapsed_seconds: float
    report_path: str = ""

    @classmethod
    def from_evaluation(
        cls,
        *,
        provider: str,
        result: EvaluationResult,
        elapsed_seconds: float,
    ) -> "BenchmarkProviderResult":
        return cls(
            provider=provider,
            total_tasks=result.total_tasks,
            completed_tasks=result.completed_tasks,
            success_rate=result.success_rate,
            total_valid_jobs=result.total_valid_jobs,
            average_pages_visited=result.average_pages_visited,
            failure_counts=result.failure_counts,
            elapsed_seconds=round(elapsed_seconds, 2),
            report_path=result.report_path.as_posix() if result.report_path else "",
        )


class BenchmarkMatrixResult(BaseModel):
    cases: list[BenchmarkCase]
    providers: list[BenchmarkProviderResult]

    @property
    def best_provider(self) -> str:
        if not self.providers:
            return ""
        best = max(
            self.providers,
            key=lambda provider: (provider.success_rate, provider.total_valid_jobs),
        )
        return best.provider


def _failure_summary(failure_counts: dict[str, int]) -> str:
    if not failure_counts:
        return "-"
    return ", ".join(f"{key}={value}" for key, value in sorted(failure_counts.items()))


def render_benchmark_markdown(result: BenchmarkMatrixResult) -> str:
    lines = [
        "# Real Site Benchmark V2",
        "",
        "## Summary",
        "",
        f"- Cases: {len(result.cases)}",
        f"- Providers: {', '.join(provider.provider for provider in result.providers)}",
        f"- Best provider: {result.best_provider or '-'}",
        "",
        "## Provider Matrix",
        "",
        "| Provider | Completed | Success Rate | Valid Jobs | Failure Counts |",
        "|---|---:|---:|---:|---|",
    ]
    for provider in result.providers:
        lines.append(
            "| "
            f"{provider.provider} | "
            f"{provider.completed_tasks}/{provider.total_tasks} | "
            f"{provider.success_rate:.2f} | "
            f"{provider.total_valid_jobs} | "
            f"{_failure_summary(provider.failure_counts)} |"
        )

    lines.extend(
        [
            "",
            "## Case Catalog",
            "",
            "| Case ID | Company | ATS | Role Family | Keyword | Location | URL | Expected Signal |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for case in result.cases:
        lines.append(
            "| "
            f"{case.case_id} | {case.company} | {case.ats} | {case.role_family} | "
            f"{case.keyword} | {case.location} | {case.url} | {case.expected_signal} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_benchmark_artifacts(
    *,
    result: BenchmarkMatrixResult,
    output_dir: str | Path,
) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "benchmark-v2.json"
    md_path = output / "benchmark-v2.md"
    json_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(render_benchmark_markdown(result), encoding="utf-8")
    return json_path, md_path
```

- [ ] **Step 4: Run tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_benchmark.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src\web_task_agent\benchmark.py tests\test_benchmark.py
git commit -m "feat: render real site benchmark matrix"
```

---

### Task 3: Implement Benchmark Provider Matrix Runner

**Files:**
- Modify: `Agent/src/web_task_agent/benchmark.py`
- Modify: `Agent/src/web_task_agent/cli.py`
- Modify: `Agent/tests/test_benchmark.py`
- Modify: `Agent/tests/test_scaffold.py`

- [ ] **Step 1: Write focused runner test with fake runner**

Append to `Agent/tests/test_benchmark.py`:

```python
import pytest

from web_task_agent.benchmark import run_benchmark_matrix


@pytest.mark.asyncio
async def test_run_benchmark_matrix_uses_each_provider_once():
    calls: list[str] = []

    async def fake_run_provider(provider, tasks, output_dir, args):
        calls.append(provider)
        return BenchmarkProviderResult(
            provider=provider,
            total_tasks=len(tasks),
            completed_tasks=len(tasks),
            success_rate=1.0,
            total_valid_jobs=len(tasks),
            average_pages_visited=1.0,
            failure_counts={},
            elapsed_seconds=0.01,
            report_path=f"{output_dir}/{provider}/evaluation-report.md",
        )

    cases = build_real_site_benchmark_v2_cases()[:2]
    result = await run_benchmark_matrix(
        cases=cases,
        providers=["baseline", "llm-demo", "deepseek"],
        output_dir="evaluations/benchmark-v2",
        args=object(),
        run_provider=fake_run_provider,
    )

    assert calls == ["baseline", "llm-demo", "deepseek"]
    assert [provider.provider for provider in result.providers] == [
        "baseline",
        "llm-demo",
        "deepseek",
    ]
    assert all(provider.total_tasks == 2 for provider in result.providers)
```

- [ ] **Step 2: Run focused test and confirm failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_benchmark.py::test_run_benchmark_matrix_uses_each_provider_once -q
```

Expected: FAIL because `run_benchmark_matrix` does not exist.

- [ ] **Step 3: Implement provider matrix runner with injectable provider runner**

Append to `Agent/src/web_task_agent/benchmark.py`:

```python
async def run_benchmark_matrix(
    *,
    cases: list[BenchmarkCase],
    providers: list[str],
    output_dir: str | Path,
    args,
    run_provider=None,
) -> BenchmarkMatrixResult:
    tasks = [case.to_evaluation_task() for case in cases]
    provider_results: list[BenchmarkProviderResult] = []
    runner = run_provider or _run_provider_with_cli_builders
    for provider in providers:
        provider_output = Path(output_dir) / provider
        result = await runner(provider, tasks, provider_output, args)
        provider_results.append(result)
    return BenchmarkMatrixResult(cases=cases, providers=provider_results)
```

Add this placeholder integration function below it; Task 3 Step 5 fills the imports in `cli.py` and calls into existing builders:

```python
async def _run_provider_with_cli_builders(provider: str, tasks: list[EvaluationTask], output_dir: Path, args):
    raise RuntimeError(
        "_run_provider_with_cli_builders must be provided by CLI wiring in this version"
    )
```

- [ ] **Step 4: Run focused test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_benchmark.py::test_run_benchmark_matrix_uses_each_provider_once -q
```

Expected: PASS.

- [ ] **Step 5: Wire CLI provider runner**

In `Agent/src/web_task_agent/cli.py`, import benchmark helpers:

```python
from web_task_agent.benchmark import (
    BenchmarkProviderResult,
    build_real_site_benchmark_v2_cases,
    parse_benchmark_providers,
    run_benchmark_matrix,
    write_benchmark_artifacts,
)
```

Add parser arguments:

```python
parser.add_argument(
    "--benchmark-v2",
    action="store_true",
    help="Run the real-site benchmark v2 provider matrix.",
)
parser.add_argument(
    "--benchmark-providers",
    default="baseline,llm-demo",
    help="Comma-separated providers: baseline,llm-demo,deepseek,qwen,qwen-vl.",
)
parser.add_argument(
    "--benchmark-limit",
    type=int,
    default=8,
    help="Limit benchmark v2 cases.",
)
parser.add_argument(
    "--benchmark-dashboard",
    action="store_true",
    help="Write a benchmark v2 HTML summary.",
)
```

Before the existing `if args.compare_llm_extractor:` block in `_run(args)`, add:

```python
if args.benchmark_v2:
    try:
        providers = parse_benchmark_providers(args.benchmark_providers)
    except ValueError as exc:
        print(str(exc))
        return 2
    try:
        result = await run_cli_benchmark_v2(args, providers=providers)
    except (LlmExtractorConfigurationError, VisualProviderConfigurationError) as exc:
        print(f"Benchmark provider is not configured: {exc}")
        return 2
    print("Real site benchmark v2")
    for provider in result.providers:
        print(
            f"{provider.provider}: "
            f"{provider.completed_tasks}/{provider.total_tasks} "
            f"success_rate={provider.success_rate:.2f}"
        )
    return 0
```

Add this function near `run_llm_extractor_comparison()`:

```python
async def run_cli_benchmark_v2(args: argparse.Namespace, *, providers: list[str]):
    cases = build_real_site_benchmark_v2_cases()[: args.benchmark_limit]

    async def run_provider(provider: str, tasks, output_dir, cli_args):
        start = perf_counter()
        if provider == "baseline":
            eval_result = await EvaluationRunner(
                output_dir,
                browser_factory=lambda task: BrowserUseClient(page_loader=HttpPageLoader()),
            ).run(tasks=tasks)
        elif provider == "llm-demo":
            eval_result = await EvaluationRunner(
                output_dir,
                browser_factory=lambda task: BrowserUseClient(page_loader=HttpPageLoader()),
                extractor_factory=lambda task: PageExtractor(
                    llm_field_extractor=DemoLlmFieldExtractor()
                ),
            ).run(tasks=tasks)
        elif provider in {"deepseek", "qwen"}:
            provider_args = argparse.Namespace(**vars(cli_args))
            provider_args.llm_extractor_provider = provider
            eval_result = await EvaluationRunner(
                output_dir,
                browser_factory=lambda task: BrowserUseClient(page_loader=HttpPageLoader()),
                extractor_factory=lambda task: PageExtractor(
                    llm_field_extractor=build_cli_llm_field_extractor(provider_args)
                ),
            ).run(tasks=tasks)
        elif provider == "qwen-vl":
            visual_provider = build_configured_visual_extractor(
                provider="qwen-vl",
                model=cli_args.visual_extractor_model,
            )
            try:
                eval_result = await EvaluationRunner(
                    output_dir,
                    browser_factory=lambda task: BrowserUseClient(page_loader=HttpPageLoader()),
                    extractor_factory=lambda task: PageExtractor(),
                    visual_extractor_factory=lambda task: visual_provider,
                ).run(tasks=tasks)
            finally:
                await visual_provider.close()
        else:
            raise ValueError(f"Unsupported benchmark provider: {provider}")
        return BenchmarkProviderResult.from_evaluation(
            provider=provider,
            result=eval_result,
            elapsed_seconds=perf_counter() - start,
        )

    result = await run_benchmark_matrix(
        cases=cases,
        providers=providers,
        output_dir=args.evaluation_dir,
        args=args,
        run_provider=run_provider,
    )
    json_path, md_path = write_benchmark_artifacts(
        result=result,
        output_dir=args.evaluation_dir,
    )
    print(f"Benchmark Markdown written to: {md_path}")
    print(f"Benchmark JSON written to: {json_path}")
    if args.benchmark_dashboard:
        dashboard_dir = HtmlDashboard(args.dashboard_dir).output_dir
        dashboard_dir.mkdir(parents=True, exist_ok=True)
        dashboard_path = dashboard_dir / "benchmark-v2.html"
        dashboard_path.write_text(
            HtmlDashboard(args.dashboard_dir).render_benchmark_summary(result),
            encoding="utf-8",
        )
        print(f"Benchmark dashboard written to: {dashboard_path}")
    return result
```

Also add at top of `cli.py`:

```python
from time import perf_counter
```

- [ ] **Step 6: Write CLI smoke test with monkeypatched benchmark runner**

Append to `Agent/tests/test_scaffold.py`:

```python
def test_cli_benchmark_v2_prints_provider_matrix(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    from web_task_agent.benchmark import (
        BenchmarkMatrixResult,
        BenchmarkProviderResult,
        build_real_site_benchmark_v2_cases,
    )

    async def fake_run_cli_benchmark_v2(args, *, providers):
        return BenchmarkMatrixResult(
            cases=build_real_site_benchmark_v2_cases()[:1],
            providers=[
                BenchmarkProviderResult(
                    provider=provider,
                    total_tasks=1,
                    completed_tasks=1 if provider != "qwen-vl" else 0,
                    success_rate=1.0 if provider != "qwen-vl" else 0.0,
                    total_valid_jobs=1 if provider != "qwen-vl" else 0,
                    average_pages_visited=1.0,
                    failure_counts={} if provider != "qwen-vl" else {"verification_filtered": 1},
                    elapsed_seconds=0.01,
                    report_path=f"evaluations/{provider}/evaluation-report.md",
                )
                for provider in providers
            ],
        )

    monkeypatch.setattr(
        "web_task_agent.cli.run_cli_benchmark_v2",
        fake_run_cli_benchmark_v2,
    )

    exit_code = main(
        [
            "--benchmark-v2",
            "--benchmark-providers",
            "baseline,llm-demo,qwen-vl",
            "--benchmark-limit",
            "1",
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Real site benchmark v2" in captured.out
    assert "baseline: 1/1 success_rate=1.00" in captured.out
    assert "llm-demo: 1/1 success_rate=1.00" in captured.out
    assert "qwen-vl: 0/1 success_rate=0.00" in captured.out
```

- [ ] **Step 7: Run focused CLI test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_benchmark_v2_prints_provider_matrix -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```powershell
git add src\web_task_agent\benchmark.py src\web_task_agent\cli.py tests\test_benchmark.py tests\test_scaffold.py
git commit -m "feat: add real site benchmark v2 matrix runner"
```

---

### Task 4: Add Benchmark HTML Summary

**Files:**
- Modify: `Agent/src/web_task_agent/dashboard.py`
- Modify: `Agent/tests/test_dashboard.py`

- [ ] **Step 1: Write failing dashboard test**

Append to `Agent/tests/test_dashboard.py`:

```python
from web_task_agent.benchmark import (
    BenchmarkMatrixResult,
    BenchmarkProviderResult,
    build_real_site_benchmark_v2_cases,
)


def test_dashboard_renders_benchmark_v2_summary():
    result = BenchmarkMatrixResult(
        cases=build_real_site_benchmark_v2_cases()[:1],
        providers=[
            BenchmarkProviderResult(
                provider="baseline",
                total_tasks=1,
                completed_tasks=0,
                success_rate=0.0,
                total_valid_jobs=0,
                average_pages_visited=1.0,
                failure_counts={"verification_filtered": 1},
                elapsed_seconds=0.1,
                report_path="evaluations/baseline/evaluation-report.md",
            ),
            BenchmarkProviderResult(
                provider="deepseek",
                total_tasks=1,
                completed_tasks=1,
                success_rate=1.0,
                total_valid_jobs=1,
                average_pages_visited=1.0,
                failure_counts={},
                elapsed_seconds=0.2,
                report_path="evaluations/deepseek/evaluation-report.md",
            ),
        ],
    )

    html = HtmlDashboard().render_benchmark_summary(result)

    assert "Real Site Benchmark V2" in html
    assert "baseline" in html
    assert "deepseek" in html
    assert "verification_filtered=1" in html
    assert "anthropic-claude-evangelist" in html
```

- [ ] **Step 2: Run test and confirm failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_dashboard.py::test_dashboard_renders_benchmark_v2_summary -q
```

Expected: FAIL because `render_benchmark_summary()` does not exist.

- [ ] **Step 3: Implement benchmark HTML renderer**

In `Agent/src/web_task_agent/dashboard.py`, import benchmark types inside type-check-safe code by using runtime method parameter without top-level import. Add this method to `HtmlDashboard`:

```python
    def render_benchmark_summary(self, result) -> str:
        provider_rows = "\n".join(
            "<tr>"
            f"<td>{escape(provider.provider)}</td>"
            f"<td>{provider.completed_tasks}/{provider.total_tasks}</td>"
            f"<td>{provider.success_rate:.2f}</td>"
            f"<td>{provider.total_valid_jobs}</td>"
            f"<td>{provider.elapsed_seconds:.2f}s</td>"
            f"<td>{escape(self._format_failure_counts(provider.failure_counts))}</td>"
            f'<td><a href="{escape(provider.report_path)}">report</a></td>'
            "</tr>"
            for provider in result.providers
        )
        if not provider_rows:
            provider_rows = '<tr><td colspan="7">No providers</td></tr>'

        case_rows = "\n".join(
            "<tr>"
            f"<td>{escape(case.case_id)}</td>"
            f"<td>{escape(case.company)}</td>"
            f"<td>{escape(case.ats)}</td>"
            f"<td>{escape(case.role_family)}</td>"
            f'<td><a href="{escape(case.url)}">{escape(case.keyword)}</a></td>'
            f"<td>{escape(case.expected_signal)}</td>"
            "</tr>"
            for case in result.cases
        )
        if not case_rows:
            case_rows = '<tr><td colspan="6">No cases</td></tr>'

        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Real Site Benchmark V2</title>
</head>
<body>
  <main>
    <h1>Real Site Benchmark V2</h1>
    <section class="metrics">
      {self._metric("Cases", len(result.cases))}
      {self._metric("Providers", len(result.providers))}
      {self._metric("Best Provider", escape(result.best_provider or "-"))}
    </section>
    <h2>Provider Matrix</h2>
    <table>
      <thead>
        <tr>
          <th>Provider</th>
          <th>Completed</th>
          <th>Success Rate</th>
          <th>Valid Jobs</th>
          <th>Elapsed</th>
          <th>Failure Counts</th>
          <th>Report</th>
        </tr>
      </thead>
      <tbody>{provider_rows}</tbody>
    </table>
    <h2>Case Catalog</h2>
    <table>
      <thead>
        <tr>
          <th>Case ID</th>
          <th>Company</th>
          <th>ATS</th>
          <th>Role Family</th>
          <th>Keyword</th>
          <th>Expected Signal</th>
        </tr>
      </thead>
      <tbody>{case_rows}</tbody>
    </table>
  </main>
</body>
</html>
"""

    def _format_failure_counts(self, failure_counts: dict[str, int]) -> str:
        if not failure_counts:
            return "-"
        return ", ".join(
            f"{key}={value}" for key, value in sorted(failure_counts.items())
        )
```

- [ ] **Step 4: Run dashboard test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_dashboard.py::test_dashboard_renders_benchmark_v2_summary -q
```

Expected: PASS.

- [ ] **Step 5: Run benchmark dashboard CLI smoke test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_benchmark_v2_prints_provider_matrix tests\test_dashboard.py::test_dashboard_renders_benchmark_v2_summary -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add src\web_task_agent\dashboard.py tests\test_dashboard.py
git commit -m "feat: render benchmark v2 dashboard"
```

---

### Task 5: Update Demo Script, README, Story, and Work Log

**Files:**
- Modify: `Agent/src/web_task_agent/cli.py`
- Modify: `Agent/README.md`
- Modify: `Agent/docs/interview-benchmark-story.md`
- Create: `Agent/docs/work-log/2026-06-29-real-site-benchmark-v2.md`
- Modify: `Agent/tests/test_scaffold.py`

- [ ] **Step 1: Add demo script assertion**

Update `test_cli_prints_demo_script` in `Agent/tests/test_scaffold.py` to include:

```python
    assert "--benchmark-v2" in captured.out
    assert "--benchmark-providers baseline,llm-demo,deepseek" in captured.out
```

- [ ] **Step 2: Add benchmark command to `print_demo_script()`**

In `Agent/src/web_task_agent/cli.py`, add this command near the real-site comparison commands:

```python
(
    r".\.venv\Scripts\web-task-agent.exe --benchmark-v2 "
    r"--benchmark-providers baseline,llm-demo,deepseek "
    r"--benchmark-limit 8 --benchmark-dashboard"
),
```

- [ ] **Step 3: Update README**

Add this section to `Agent/README.md`:

```markdown
## Real Site Benchmark V2

`--benchmark-v2` runs a provider matrix over the real-site benchmark catalog. It is the main interview artifact for explaining how the project behaves beyond deterministic fixtures.

```powershell
.\.venv\Scripts\web-task-agent.exe --benchmark-v2 --benchmark-providers baseline,llm-demo,deepseek --benchmark-limit 8 --benchmark-dashboard
```

Outputs:

- `evaluations/benchmark-v2.json`: machine-readable case catalog and provider matrix.
- `evaluations/benchmark-v2.md`: Markdown report with provider success rates and failure categories.
- `dashboards/benchmark-v2.html`: local HTML summary for interview/demo review.
- `evaluations/<provider>/evaluation-report.md`: per-provider evaluation detail.

Use `--benchmark-providers baseline,llm-demo,deepseek,qwen,qwen-vl` when the relevant API keys and sibling `visual-web-agent` package are configured. Live public job URLs can drift; failures are recorded as benchmark data through `failure_counts` rather than hidden.
```

- [ ] **Step 4: Update interview story**

Append to `Agent/docs/interview-benchmark-story.md`:

```markdown
## Real Site Benchmark V2 讲法

下一阶段我把真实站点评测从一次性 comparison 升级成 provider matrix。每个样本不仅有 URL，还有公司、ATS 类型、岗位族、期望信号和技能标签；每个 provider 都跑同一批样本，输出完成率、有效岗位数、失败分类和耗时。

面试时重点不是说某个 provider 永远最好，而是说明我如何设计可复现评测：固定样本目录、统一 workflow、统一 verifier、统一失败分类，再把 rule、LLM、visual provider 放在同一张矩阵里比较。真实页面可能变化，所以系统把 HTTP、空页面、抽取失败、verifier 过滤都记录下来，这比只展示一次成功 demo 更能体现工程判断。
```

- [ ] **Step 5: Create work log**

Create `Agent/docs/work-log/2026-06-29-real-site-benchmark-v2.md`:

```markdown
# 本轮工作：Real Site Benchmark V2

## 完成了什么

- 新增真实站点评测样本目录，记录公司、ATS、岗位族、URL、技能标签和期望信号。
- 新增 benchmark provider matrix，复用现有 EvaluationRunner 跑 baseline、llm-demo、DeepSeek、Qwen、Qwen-VL 等 provider。
- 新增 Markdown、JSON 和 HTML benchmark artifacts。
- 将 benchmark 命令加入 demo script 和 README。

## 你要理解什么

这不是新增一个抽取器，而是把现有抽取能力放进可比较、可复盘的评测框架里。真实网页会漂移，所以 benchmark 的价值是记录成功率和失败原因，而不是假设每个 URL 永远稳定。

## 你现在应该做什么

优先运行：

```powershell
.\.venv\Scripts\web-task-agent.exe --benchmark-v2 --benchmark-providers baseline,llm-demo,deepseek --benchmark-limit 8 --benchmark-dashboard
```

然后查看：

- `evaluations/benchmark-v2.md`
- `evaluations/benchmark-v2.json`
- `dashboards/benchmark-v2.html`
```

- [ ] **Step 6: Run docs/demo test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_prints_demo_script -q
.\.venv\Scripts\web-task-agent.exe --print-demo-script
```

Expected: test passes and printed commands include `--benchmark-v2`.

- [ ] **Step 7: Commit**

Run:

```powershell
git add src\web_task_agent\cli.py tests\test_scaffold.py README.md docs\interview-benchmark-story.md docs\work-log\2026-06-29-real-site-benchmark-v2.md
git commit -m "docs: add real site benchmark v2 workflow"
```

---

### Task 6: Final Verification

**Files:**
- No new implementation files.

- [ ] **Step 1: Run full automated tests**

Run:

```powershell
cd C:\Users\13993\Desktop\大模型学习\Agent
.\.venv\Scripts\python.exe -m pytest
```

Expected: all tests pass.

- [ ] **Step 2: Run benchmark v2 without external LLM keys**

Run:

```powershell
.\.venv\Scripts\web-task-agent.exe --benchmark-v2 --benchmark-providers baseline,llm-demo --benchmark-limit 2 --benchmark-dashboard
```

Expected:

```text
Benchmark Markdown written to:
Benchmark JSON written to:
Benchmark dashboard written to:
Real site benchmark v2
baseline:
llm-demo:
```

Expected files:

```text
evaluations/benchmark-v2.md
evaluations/benchmark-v2.json
dashboards/benchmark-v2.html
```

- [ ] **Step 3: Run benchmark v2 with DeepSeek provider if configured**

Run:

```powershell
.\.venv\Scripts\web-task-agent.exe --benchmark-v2 --benchmark-providers baseline,llm-demo,deepseek --benchmark-limit 8 --benchmark-dashboard
```

Expected if `DEEPSEEK_API_KEY` is configured:

```text
deepseek:
```

Expected if not configured:

```text
Benchmark provider is not configured:
```

Exit code should be `2` for missing provider config, not a silent partial success.

- [ ] **Step 4: Run benchmark v2 with visual provider if configured**

Run:

```powershell
.\.venv\Scripts\web-task-agent.exe --benchmark-v2 --benchmark-providers baseline,llm-demo,qwen-vl --benchmark-limit 2 --benchmark-dashboard
```

Expected if `DASHSCOPE_API_KEY` and `visual-web-agent` are configured:

```text
qwen-vl:
```

No Windows `unclosed transport` warnings should appear.

- [ ] **Step 5: Inspect JSON payload**

Run:

```powershell
Get-Content -Raw evaluations\benchmark-v2.json | ConvertFrom-Json | Select-Object -ExpandProperty providers
Get-Content -Raw evaluations\benchmark-v2.json | ConvertFrom-Json | Select-Object -ExpandProperty cases
```

Expected:

```text
provider
completed_tasks
success_rate
failure_counts
case_id
company
ats
role_family
url
```

- [ ] **Step 6: Check git status**

Run:

```powershell
git status --short
```

Expected: clean worktree after commits.

---

## Self-Review

- Spec coverage: The plan covers catalog metadata, provider matrix execution, Markdown/JSON/HTML artifacts, CLI/demo script, README/story/work-log updates, and final verification.
- Placeholder scan: No `TBD`, `TODO`, unspecified “add tests”, or “handle edge cases” placeholders remain.
- Type consistency: All new names are defined before use: `BenchmarkCase`, `BenchmarkProviderResult`, `BenchmarkMatrixResult`, `parse_benchmark_providers`, `run_benchmark_matrix`, `write_benchmark_artifacts`, and `render_benchmark_summary`.
- Scope check: This stays focused on benchmark/reporting. It does not add new extractors, new matching logic, or browser automation beyond existing provider paths.
