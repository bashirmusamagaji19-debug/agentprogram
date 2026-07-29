# Visual Extractor Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a narrow visual extraction experiment path to `web-task-agent` so seed URLs can be extracted through a screenshot/VLM-compatible adapter and evaluated against the existing text extractor path.

**Architecture:** Keep `Agent` as the main workflow and add a small async visual extraction adapter inside `web_task_agent`. The adapter converts visual job fields into the existing `JobPosting` model, and `WebTaskWorkflow` uses it only when explicitly configured. The first milestone supports seed URL workflows and evaluation comparison; it does not merge the whole `visual-web-agent` package into `Agent`.

**Tech Stack:** Python 3.11+, Pydantic, pytest, existing `web_task_agent` CLI/workflow/evaluation modules, existing `visual-web-agent` concepts as reference.

---

## File Structure

- Create: `src/web_task_agent/visual_extractor.py`
  - Defines `VisualJobFields`, `VisualExtractionResult`, `AsyncVisualJobExtractor`, `DemoVisualJobExtractor`, and `job_from_visual_fields`.
  - Keeps the first integration independent from the external `visual-web-agent` package.
- Modify: `src/web_task_agent/workflow.py`
  - Accepts an optional async visual extractor.
  - Makes `_extractor_node` async.
  - Records visual extraction metadata and falls back to text extraction on visual failure.
- Modify: `src/web_task_agent/cli.py`
  - Adds `--visual-extractor-demo`.
  - Wires the demo visual extractor into workflow and evaluation.
  - Adds visual extractor comparison support to `--compare-llm-extractor` output as a new extractor row named `visual_demo`.
- Modify: `src/web_task_agent/evaluation.py`
  - No structural change is expected unless tests expose that `EvaluationRunner` assumes synchronous extractor nodes indirectly. If needed, update only the factory usage path.
- Create: `tests/test_visual_extractor.py`
  - Unit tests for converting visual fields to `JobPosting`.
- Modify: `tests/test_workflow.py`
  - Tests visual extraction in seed URL mode and fallback behavior.
- Modify: `tests/test_scaffold.py`
  - CLI tests for `--visual-extractor-demo` and comparison JSON/report output.
- Modify: `README.md`
  - Adds a short command section for visual extractor demo.
- Create: `docs/work-log/2026-06-29-visual-extractor-integration.md`
  - Records the implementation scope, verification commands, and current limitations.

---

### Task 1: Add Visual Extraction Domain Adapter

**Files:**
- Create: `src/web_task_agent/visual_extractor.py`
- Create: `tests/test_visual_extractor.py`

- [ ] **Step 1: Write failing unit tests**

Add `tests/test_visual_extractor.py`:

```python
import pytest

from web_task_agent.models import BrowserPage
from web_task_agent.visual_extractor import (
    DemoVisualJobExtractor,
    VisualJobFields,
    job_from_visual_fields,
)


def test_job_from_visual_fields_maps_to_job_posting():
    page = BrowserPage(
        url="https://example.com/jobs/visual-ai-intern",
        title="Careers",
        content="",
        source="visual-demo",
    )
    fields = VisualJobFields(
        title="Visual AI Intern",
        company="Example Vision",
        location="Remote",
        requirements="Python, Playwright, Qwen-VL",
        responsibilities="Extract job fields from screenshots",
        skills=["Python", "Playwright", "Qwen-VL"],
        confidence=0.83,
    )

    job = job_from_visual_fields(page=page, fields=fields)

    assert job.title == "Visual AI Intern"
    assert job.company == "Example Vision"
    assert job.location == "Remote"
    assert job.source == "visual-demo"
    assert job.url == "https://example.com/jobs/visual-ai-intern"
    assert job.requirements == "Python, Playwright, Qwen-VL"
    assert job.responsibilities == "Extract job fields from screenshots"
    assert job.skills == ["Python", "Playwright", "Qwen-VL"]
    assert job.confidence == 0.83


def test_job_from_visual_fields_fills_safe_unknowns():
    page = BrowserPage(
        url="https://example.com/jobs/partial",
        title="Fallback Screenshot Title",
        content="",
        source="visual-demo",
    )
    fields = VisualJobFields(requirements="Python, LLM", confidence=0.4)

    job = job_from_visual_fields(page=page, fields=fields)

    assert job.title == "Fallback Screenshot Title"
    assert job.company == "Unknown Company"
    assert job.location == "Unknown Location"
    assert job.skills == ["Python", "LLM"]
    assert job.confidence == 0.4


@pytest.mark.asyncio
async def test_demo_visual_extractor_returns_structured_fields_for_known_seed_url():
    extractor = DemoVisualJobExtractor()
    page = BrowserPage(
        url="https://example.com/jobs/visual-ai-intern",
        title="Careers",
        content="",
        source="fixture",
    )

    result = await extractor.extract(page)

    assert result.success is True
    assert result.fields is not None
    assert result.fields.title == "Visual AI Intern"
    assert result.fields.company == "Example Vision"
    assert result.fields.confidence >= 0.8


@pytest.mark.asyncio
async def test_demo_visual_extractor_reports_unknown_url_failure():
    extractor = DemoVisualJobExtractor()
    page = BrowserPage(
        url="https://example.com/jobs/unknown",
        title="Unknown",
        content="",
        source="fixture",
    )

    result = await extractor.extract(page)

    assert result.success is False
    assert result.fields is None
    assert "No demo visual fixture" in result.error
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_visual_extractor.py -q
```

Expected: FAIL because `web_task_agent.visual_extractor` does not exist.

- [ ] **Step 3: Implement visual adapter**

Create `src/web_task_agent/visual_extractor.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from web_task_agent.models import BrowserPage, JobPosting


class VisualJobFields(BaseModel):
    title: str = ""
    company: str = ""
    location: str = ""
    requirements: str = ""
    responsibilities: str = ""
    skills: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


@dataclass
class VisualExtractionResult:
    url: str
    success: bool
    fields: VisualJobFields | None
    error: str = ""


class AsyncVisualJobExtractor(Protocol):
    async def extract(self, page: BrowserPage) -> VisualExtractionResult:
        """Extract visual job fields from a browser page."""


def job_from_visual_fields(*, page: BrowserPage, fields: VisualJobFields) -> JobPosting:
    requirements = fields.requirements.strip()
    skills = fields.skills or [
        skill.strip()
        for skill in requirements.replace("，", ",").split(",")
        if skill.strip()
    ]
    return JobPosting(
        title=fields.title.strip() or page.title or "Unknown Title",
        company=fields.company.strip() or "Unknown Company",
        location=fields.location.strip() or "Unknown Location",
        source="visual-demo",
        url=page.url,
        requirements=requirements,
        responsibilities=fields.responsibilities.strip(),
        skills=skills,
        confidence=fields.confidence,
    )


class DemoVisualJobExtractor:
    def __init__(self) -> None:
        self._fixtures = {
            "https://example.com/jobs/visual-ai-intern": VisualJobFields(
                title="Visual AI Intern",
                company="Example Vision",
                location="Remote",
                requirements="Python, Playwright, Qwen-VL",
                responsibilities="Extract job fields from screenshots",
                skills=["Python", "Playwright", "Qwen-VL"],
                confidence=0.86,
            ),
            "https://example.com/jobs/unstructured-ai-agent-intern": VisualJobFields(
                title="AI Agent Intern",
                company="Example Robotics",
                location="Remote",
                requirements="Python, LangGraph, LLM evaluation",
                responsibilities="Build browser agents from screenshot evidence",
                skills=["Python", "LangGraph", "LLM evaluation"],
                confidence=0.84,
            ),
        }

    async def extract(self, page: BrowserPage) -> VisualExtractionResult:
        fields = self._fixtures.get(page.url)
        if fields is None:
            return VisualExtractionResult(
                url=page.url,
                success=False,
                fields=None,
                error=f"No demo visual fixture for URL: {page.url}",
            )
        return VisualExtractionResult(url=page.url, success=True, fields=fields)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_visual_extractor.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src\web_task_agent\visual_extractor.py tests\test_visual_extractor.py
git commit -m "feat: add visual extraction adapter"
```

---

### Task 2: Wire Visual Extractor Into Workflow

**Files:**
- Modify: `src/web_task_agent/workflow.py`
- Modify: `tests/test_workflow.py`

- [ ] **Step 1: Write failing workflow tests**

Append to `tests/test_workflow.py`:

```python
from web_task_agent.visual_extractor import VisualExtractionResult, VisualJobFields


@pytest.mark.asyncio
async def test_workflow_uses_visual_extractor_for_seed_url_pages(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()

    class SeedBrowser:
        async def search(self, query: str, target_count: int):
            raise AssertionError("search should not be called")

        async def open_url(self, url: str):
            return FAKE_JOB_PAGES[0].model_copy(update={"url": url, "content": ""})

    class FakeVisualExtractor:
        async def extract(self, page):
            return VisualExtractionResult(
                url=page.url,
                success=True,
                fields=VisualJobFields(
                    title="Visual AI Intern",
                    company="Example Vision",
                    location="Remote",
                    requirements="Python, Playwright, Qwen-VL",
                    responsibilities="Extract job fields from screenshots",
                    skills=["Python", "Playwright", "Qwen-VL"],
                    confidence=0.9,
                ),
            )

    workflow = WebTaskWorkflow(
        browser=SeedBrowser(),
        extractor=PageExtractor(),
        visual_extractor=FakeVisualExtractor(),
        matcher=JobMatcher(),
        verifier=JobVerifier(required_keywords=["AI", "LLM", "Agent", "Visual"]),
        repository=repo,
        reporter=MarkdownReporter(output_dir=tmp_path / "reports"),
    )

    state = await workflow.run(
        UserProfile(
            keyword="seed URLs",
            target_count=1,
            skills=["Python"],
            seed_urls=["https://example.com/jobs/visual-ai-intern"],
        ),
        run_id="run-visual",
    )

    assert state.jobs[0].title == "Visual AI Intern"
    assert state.jobs[0].source == "visual-demo"
    assert state.metadata["extractor_mode"] == "visual-demo"
    assert state.metadata["visual_extraction"]["successes"] == 1
    assert state.metadata["visual_extraction"]["failures"] == 0
    assert any("visual extracted 1" in item["summary"] for item in state.metadata["execution_trace"])


@pytest.mark.asyncio
async def test_workflow_falls_back_to_text_extractor_when_visual_extractor_fails(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()

    class SeedBrowser:
        async def search(self, query: str, target_count: int):
            raise AssertionError("search should not be called")

        async def open_url(self, url: str):
            return FAKE_JOB_PAGES[0].model_copy(update={"url": url})

    class FailingVisualExtractor:
        async def extract(self, page):
            return VisualExtractionResult(
                url=page.url,
                success=False,
                fields=None,
                error="visual fixture missing",
            )

    workflow = WebTaskWorkflow(
        browser=SeedBrowser(),
        extractor=PageExtractor(),
        visual_extractor=FailingVisualExtractor(),
        matcher=JobMatcher(),
        verifier=JobVerifier(required_keywords=["AI", "LLM", "Agent"]),
        repository=repo,
        reporter=MarkdownReporter(output_dir=tmp_path / "reports"),
    )

    state = await workflow.run(
        UserProfile(
            keyword="seed URLs",
            target_count=1,
            skills=["Python"],
            seed_urls=["https://example.com/jobs/text-fallback"],
        ),
        run_id="run-visual-fallback",
    )

    assert state.metadata["visual_extraction"]["successes"] == 0
    assert state.metadata["visual_extraction"]["failures"] == 1
    assert state.metadata["visual_extraction"]["errors"] == [
        {
            "url": "https://example.com/jobs/text-fallback",
            "error": "visual fixture missing",
        }
    ]
    assert state.metadata["jobs_found"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_workflow.py::test_workflow_uses_visual_extractor_for_seed_url_pages tests\test_workflow.py::test_workflow_falls_back_to_text_extractor_when_visual_extractor_fails -q
```

Expected: FAIL because `WebTaskWorkflow.__init__` does not accept `visual_extractor`.

- [ ] **Step 3: Implement workflow changes**

Modify `src/web_task_agent/workflow.py`:

```python
from web_task_agent.visual_extractor import AsyncVisualJobExtractor, job_from_visual_fields
```

Update `WebTaskWorkflow.__init__`:

```python
    def __init__(
        self,
        *,
        browser: BrowserClient,
        extractor: PageExtractor,
        matcher: JobMatcher,
        verifier: JobVerifier,
        repository: JobRepository,
        reporter: MarkdownReporter,
        visual_extractor: AsyncVisualJobExtractor | None = None,
    ) -> None:
        self.browser = browser
        self.extractor = extractor
        self.visual_extractor = visual_extractor
        self.matcher = matcher
        self.verifier = verifier
        self.repository = repository
        self.reporter = reporter
```

Update sequential `run`:

```python
        state = await self._extractor_node(state)
```

Replace `_extractor_node`:

```python
    async def _extractor_node(self, state: WorkflowState) -> WorkflowState:
        state.candidate_urls = [page.url for page in state.pages]
        extracted_jobs = []
        visual_stats = {
            "successes": 0,
            "failures": 0,
            "errors": [],
        }
        for page in state.pages:
            if self.visual_extractor is not None:
                visual_result = await self.visual_extractor.extract(page)
                if visual_result.success and visual_result.fields is not None:
                    extracted_jobs.append(
                        job_from_visual_fields(page=page, fields=visual_result.fields)
                    )
                    visual_stats["successes"] += 1
                    continue
                visual_stats["failures"] += 1
                visual_stats["errors"].append(
                    {"url": page.url, "error": visual_result.error}
                )
            extracted_jobs.append(self.extractor.extract(page))
        state.metadata["extracted_jobs"] = extracted_jobs
        if self.visual_extractor is not None:
            state.metadata["extractor_mode"] = "visual-demo"
            state.metadata["visual_extraction"] = visual_stats
            self._record_trace(
                state,
                "extractor",
                (
                    f"visual extracted {visual_stats['successes']} job candidates; "
                    f"fell back {visual_stats['failures']} times"
                ),
            )
            return state
        self._record_trace(
            state,
            "extractor",
            f"extracted {len(state.metadata['extracted_jobs'])} job candidates",
        )
        return state
```

LangGraph can call async node methods, so no additional graph API change is needed.

- [ ] **Step 4: Run workflow tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_workflow.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src\web_task_agent\workflow.py tests\test_workflow.py
git commit -m "feat: support visual extractor in workflow"
```

---

### Task 3: Add CLI Flag for Visual Extractor Demo

**Files:**
- Modify: `src/web_task_agent/cli.py`
- Modify: `tests/test_scaffold.py`

- [ ] **Step 1: Write failing CLI test**

Append to `tests/test_scaffold.py` near other CLI demo tests:

```python
def test_cli_seed_url_can_use_visual_extractor_demo(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--seed-url",
                "https://example.com/jobs/visual-ai-intern",
                "--target-count",
                "1",
                "--demo",
                "--visual-extractor-demo",
                "--json-output",
                "outputs/visual-demo.json",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Visual extractor demo: enabled" in captured.out
    assert "Valid jobs: 1" in captured.out
    payload = json.loads(
        (tmp_path / "outputs" / "visual-demo.json").read_text(encoding="utf-8")
    )
    assert payload["metadata"]["extractor_mode"] == "visual-demo"
    assert payload["metadata"]["visual_extraction"]["successes"] == 1
    assert payload["jobs"][0]["title"] == "Visual AI Intern"
    assert payload["jobs"][0]["company"] == "Example Vision"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_seed_url_can_use_visual_extractor_demo -q
```

Expected: FAIL because `--visual-extractor-demo` is unknown.

- [ ] **Step 3: Implement CLI flag and workflow wiring**

Modify imports in `src/web_task_agent/cli.py`:

```python
from web_task_agent.visual_extractor import DemoVisualJobExtractor
```

Add parser argument in `build_parser()` after `--llm-extractor-model`:

```python
    parser.add_argument(
        "--visual-extractor-demo",
        action="store_true",
        help="Use deterministic screenshot-style visual job extraction for seed URL experiments.",
    )
```

Update `build_workflow` signature:

```python
def build_workflow(
    *,
    browser,
    db_path: str,
    report_dir: str,
    llm_field_extractor=None,
    llm_matcher=None,
    visual_extractor=None,
) -> WebTaskWorkflow:
```

Pass `visual_extractor` into `WebTaskWorkflow`:

```python
        visual_extractor=visual_extractor,
```

Add helper:

```python
def build_cli_visual_extractor(args: argparse.Namespace):
    if args.visual_extractor_demo:
        return DemoVisualJobExtractor()
    return None
```

In the main non-evaluation workflow path, build and pass it:

```python
    visual_extractor = build_cli_visual_extractor(args)
    workflow = build_workflow(
        browser=browser,
        db_path=args.db_path,
        report_dir=args.report_dir,
        llm_field_extractor=llm_field_extractor,
        llm_matcher=llm_matcher,
        visual_extractor=visual_extractor,
    )
```

After the workflow run, print the visual mode:

```python
    if args.visual_extractor_demo:
        print("Visual extractor demo: enabled")
        state.metadata["extractor_mode"] = "visual-demo"
```

Also pass `visual_extractor=None` in existing `build_workflow` calls for `--export-graph` and interactive paths only when needed by function signature. Existing default handles omitted values.

- [ ] **Step 4: Run CLI test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_seed_url_can_use_visual_extractor_demo -q
```

Expected: PASS.

- [ ] **Step 5: Run related smoke tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_demo_mode_can_use_llm_extractor_demo tests\test_scaffold.py::test_cli_demo_mode_writes_json_output tests\test_workflow.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add src\web_task_agent\cli.py tests\test_scaffold.py
git commit -m "feat: add visual extractor demo CLI"
```

---

### Task 4: Add Visual Demo to Extractor Comparison

**Files:**
- Modify: `src/web_task_agent/cli.py`
- Modify: `tests/test_scaffold.py`

- [ ] **Step 1: Write failing comparison test**

Append to `tests/test_scaffold.py` near `--compare-llm-extractor` tests:

```python
def test_cli_compare_extractor_can_include_visual_demo(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--compare-llm-extractor",
                "--seed-url",
                "https://example.com/jobs/visual-ai-intern",
                "--visual-extractor-demo",
                "--json-output",
                "evaluations/visual-comparison.json",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "visual-demo: 1/1" in captured.out
    payload = json.loads(
        (tmp_path / "evaluations" / "visual-comparison.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["extractors"]["visual_demo"]["completed_tasks"] == 1
    report = (tmp_path / payload["report_path"]).read_text(encoding="utf-8")
    assert "| visual_demo | 1 | 1 | 1.00 |" in report
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_compare_extractor_can_include_visual_demo -q
```

Expected: FAIL because comparison does not include `visual_demo`.

- [ ] **Step 3: Implement comparison support**

In `_run`, after printing `llm-demo`, add:

```python
        if args.visual_extractor_demo:
            visual_result = result["visual_demo"]
            print(
                "visual-demo: "
                f"{visual_result['completed_tasks']}/{visual_result['total_tasks']}"
            )
```

In `run_llm_extractor_comparison`, after `llm_demo` result:

```python
    if args.visual_extractor_demo:
        visual_demo = await EvaluationRunner(
            args.evaluation_dir,
            browser_factory=browser_factory,
            extractor_factory=lambda task: PageExtractor(),
            visual_extractor_factory=lambda task: DemoVisualJobExtractor(),
        ).run(tasks=tasks)
        extractors["visual_demo"] = visual_demo.model_dump(mode="json")
```

This requires `EvaluationRunner` support for a visual extractor factory. If `EvaluationRunner` does not accept this parameter, implement Task 4 Step 4 before rerunning the test.

- [ ] **Step 4: Extend EvaluationRunner only if needed**

Modify `src/web_task_agent/evaluation.py` constructor:

```python
    def __init__(
        self,
        output_dir: str | Path,
        *,
        browser_factory=None,
        extractor_factory=None,
        visual_extractor_factory=None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.browser_factory = browser_factory
        self.extractor_factory = extractor_factory
        self.visual_extractor_factory = visual_extractor_factory
```

In the place where `WebTaskWorkflow` is constructed inside `EvaluationRunner`, pass:

```python
visual_extractor=(
    self.visual_extractor_factory(task)
    if self.visual_extractor_factory is not None
    else None
),
```

Update tests that create fake `EvaluationRunner` classes in `tests/test_scaffold.py` only if they fail because their constructors do not accept `visual_extractor_factory`; the concrete fix is to add `visual_extractor_factory=None` to those fake `__init__` signatures.

- [ ] **Step 5: Add `visual_demo` to result mapping**

In `run_llm_extractor_comparison`, after building `result`, add:

```python
    if args.visual_extractor_demo:
        result["visual_demo"] = extractors["visual_demo"]
```

- [ ] **Step 6: Run comparison test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_compare_extractor_can_include_visual_demo -q
```

Expected: PASS.

- [ ] **Step 7: Run evaluation and comparison tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_evaluation.py tests\test_scaffold.py::test_cli_compare_llm_extractor_writes_comparison_json tests\test_scaffold.py::test_cli_compare_extractor_can_include_visual_demo -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```powershell
git add src\web_task_agent\cli.py src\web_task_agent\evaluation.py tests\test_scaffold.py
git commit -m "feat: compare visual extractor demo"
```

---

### Task 5: Documentation and Work Log

**Files:**
- Modify: `README.md`
- Create: `docs/work-log/2026-06-29-visual-extractor-integration.md`

- [ ] **Step 1: Update README command section**

Add this block under the local run command examples in `README.md`:

```markdown
### Visual extractor demo

The visual extractor path is an experimental seed-URL mode for testing screenshot/VLM-style extraction without changing the main text extraction path.

```powershell
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/visual-ai-intern" --demo --target-count 1 --visual-extractor-demo --json-output outputs\visual-demo.json
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --seed-url "https://example.com/jobs/visual-ai-intern" --visual-extractor-demo --json-output evaluations\visual-comparison.json
```

Current scope:

- Uses deterministic visual fixtures for repeatable local verification.
- Produces normal `JobPosting` objects, so verifier, matcher, reports, dashboards, and JSON output continue to work.
- Falls back to text extraction when visual extraction fails for a page.
```

- [ ] **Step 2: Create work log**

Create `docs/work-log/2026-06-29-visual-extractor-integration.md`:

```markdown
# 本轮工作：Visual Extractor 最小接入计划

## 目标

把 `visual-web-agent` 的截图/VLM 抽取思路以最小实验路径接入 `Agent`，先支持 seed URL 和评测对比，不直接合并两个项目。

## 实施范围

- 新增 `web_task_agent.visual_extractor` 适配层。
- `WebTaskWorkflow` 支持可选 async visual extractor。
- CLI 新增 `--visual-extractor-demo`。
- `--compare-llm-extractor` 可以额外输出 `visual_demo` 对比行。
- 保持默认文本抽取路径不变。

## 验证命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_visual_extractor.py tests\test_workflow.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_seed_url_can_use_visual_extractor_demo tests\test_scaffold.py::test_cli_compare_extractor_can_include_visual_demo -q
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/visual-ai-intern" --demo --target-count 1 --visual-extractor-demo --json-output outputs\visual-demo.json
```

## 当前边界

- 本轮只接 deterministic visual demo，不直接调用真实 Qwen-VL。
- 真实截图链路继续保留在 `visual-web-agent` 项目中，等 Agent 侧接口稳定后再决定迁移或作为依赖调用。
- 视觉抽取失败时回退到文本抽取，避免破坏现有 demo 和评测闭环。
```

- [ ] **Step 3: Run documentation-related smoke command**

Run:

```powershell
.\.venv\Scripts\web-task-agent.exe --print-demo-script
```

Expected: exits with code 0 and prints the existing demo script. No README parsing is required.

- [ ] **Step 4: Commit**

Run:

```powershell
git add README.md docs\work-log\2026-06-29-visual-extractor-integration.md
git commit -m "docs: document visual extractor demo"
```

---

### Task 6: Final Verification

**Files:**
- No new files.

- [ ] **Step 1: Run full test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Expected: all tests pass.

- [ ] **Step 2: Run visual demo CLI**

Run:

```powershell
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/visual-ai-intern" --demo --target-count 1 --visual-extractor-demo --json-output outputs\visual-demo.json
```

Expected output includes:

```text
Visual extractor demo: enabled
Report written to:
Valid jobs: 1
JSON output written to: outputs\visual-demo.json
```

- [ ] **Step 3: Inspect JSON metadata**

Run:

```powershell
Get-Content -Raw outputs\visual-demo.json | ConvertFrom-Json | Select-Object -ExpandProperty metadata
```

Expected metadata includes:

```text
extractor_mode : visual-demo
visual_extraction : @{successes=1; failures=0; errors=System.Object[]}
```

- [ ] **Step 4: Run visual comparison CLI**

Run:

```powershell
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --seed-url "https://example.com/jobs/visual-ai-intern" --visual-extractor-demo --json-output evaluations\visual-comparison.json
```

Expected output includes:

```text
LLM extractor comparison
baseline:
llm-demo:
visual-demo: 1/1
Comparison report written to:
Comparison JSON written to: evaluations\visual-comparison.json
```

- [ ] **Step 5: Check git status**

Run:

```powershell
git status --short
```

Expected: clean working tree after commits.

