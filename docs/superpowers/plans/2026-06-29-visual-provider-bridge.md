# Real Visual Provider Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real Qwen-VL visual extraction path in `visual-web-agent` and expose it to `web-task-agent` as an optional provider for seed URL workflows, evaluation, and extractor comparison.

**Architecture:** Keep `visual-web-agent` as the owner of screenshot + Playwright + VLM wiring, but extract a reusable factory so other code can build the real extractor without depending on CLI internals. In `Agent`, add a narrow bridge that adapts the external visual extractor result back into the existing `AsyncVisualJobExtractor` protocol and `JobPosting` model. Seed-URL workflows and evaluation keep the same verifier/matcher/report pipeline; the only change is which extractor backend is selected.

**Tech Stack:** Python 3.11+, Playwright, DashScope/Qwen-VL, Pydantic, pytest, argparse, existing `web_task_agent` workflow/evaluation modules, existing `visual_web_agent` package.

---

## File Structure

- Create: `visual-web-agent/src/visual_web_agent/factory.py`
  - Reusable builder for demo and real visual extractors.
- Modify: `visual-web-agent/src/visual_web_agent/cli.py`
  - Uses the factory helpers instead of hand-wiring browser/VLM/parser in place.
- Create: `visual-web-agent/tests/test_factory.py`
  - Factory and wiring tests for demo and injected real-provider components.
- Create: `Agent/src/web_task_agent/visual_provider.py`
  - Runtime bridge from `visual_web_agent` results into `web_task_agent` visual extraction types.
- Modify: `Agent/src/web_task_agent/cli.py`
  - Adds `--visual-extractor-provider` and routes provider selection into workflow/evaluation/comparison paths.
- Modify: `Agent/src/web_task_agent/evaluation.py`
  - Thread the visual provider factory into `WebTaskWorkflow` construction when requested.
- Create: `Agent/tests/test_visual_provider.py`
  - Bridge tests for conversion, missing-package errors, and provider selection.
- Modify: `Agent/tests/test_scaffold.py`
  - CLI smoke tests for the real visual provider and comparison path.
- Modify: `Agent/README.md`
  - Document the sibling-package install and real visual provider commands.
- Modify: `visual-web-agent/README.md`
  - Document the reusable extractor factory and real-provider entrypoints.
- Create: `Agent/docs/work-log/2026-06-29-visual-provider-bridge.md`
  - Records the integration scope, verification commands, and rollout notes.

---

### Task 1: Extract Reusable Visual Provider Factory

**Files:**
- Create: `visual-web-agent/src/visual_web_agent/factory.py`
- Modify: `visual-web-agent/src/visual_web_agent/cli.py`
- Create: `visual-web-agent/tests/test_factory.py`

- [ ] **Step 1: Write failing factory tests**

Create `visual-web-agent/tests/test_factory.py`:

```python
import pytest

from visual_web_agent.browser import FakeVisualBrowser, PlaywrightBrowserClient
from visual_web_agent.factory import build_visual_job_extractor
from visual_web_agent.vlm import FakeVlmClient, QwenVlClient


def test_build_visual_job_extractor_uses_injected_demo_components():
    browser = FakeVisualBrowser()
    vlm = FakeVlmClient()

    extractor = build_visual_job_extractor(browser=browser, vlm=vlm)

    assert extractor.parser.browser is browser
    assert extractor.parser.vlm is vlm


def test_build_visual_job_extractor_defaults_to_real_components(monkeypatch):
    created = []

    class FakeRealBrowser(PlaywrightBrowserClient):
        def __init__(self, *args, **kwargs):
            created.append("browser")

    class FakeRealVlm(QwenVlClient):
        def __init__(self, *args, **kwargs):
            created.append("vlm")

    extractor = build_visual_job_extractor(
        browser_cls=FakeRealBrowser,
        vlm_cls=FakeRealVlm,
        api_key="test-key",
    )

    assert created == ["browser", "vlm"]
    assert extractor.parser.browser.__class__.__name__ == "FakeRealBrowser"
    assert extractor.parser.vlm.__class__.__name__ == "FakeRealVlm"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_factory.py -q
```

Expected: FAIL because `visual_web_agent.factory` does not exist yet.

- [ ] **Step 3: Implement the factory**

Create `visual-web-agent/src/visual_web_agent/factory.py`:

```python
from __future__ import annotations

from visual_web_agent.browser import PlaywrightBrowserClient, VisualBrowserClient
from visual_web_agent.extractor import VisualJobExtractor
from visual_web_agent.parser import VisualPageParser
from visual_web_agent.vlm import QwenVlClient, VlmClient


def build_visual_job_extractor(
    *,
    browser: VisualBrowserClient | None = None,
    vlm: VlmClient | None = None,
    browser_cls=PlaywrightBrowserClient,
    vlm_cls=QwenVlClient,
    api_key: str | None = None,
    model: str = "qwen-vl-plus",
) -> VisualJobExtractor:
    """Build a visual extractor with injectable browser/VLM dependencies."""
    browser = browser or browser_cls()
    if vlm is None:
        vlm = vlm_cls(api_key=api_key, model=model)
    parser = VisualPageParser(browser=browser, vlm=vlm)
    return VisualJobExtractor(parser=parser)
```

Update `visual-web-agent/src/visual_web_agent/cli.py`:

```python
from visual_web_agent.factory import build_visual_job_extractor
```

Replace inline real-mode wiring in `_run_single()` and `_run_batch()` with:

```python
extractor = build_visual_job_extractor()
```

For demo mode, keep `FakeVisualBrowser` + `FakeVlmClient`, but still build the parser/extractor through the same factory:

```python
extractor = build_visual_job_extractor(browser=browser, vlm=vlm)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_factory.py tests\test_parser.py tests\test_extractor.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src\visual_web_agent\factory.py src\visual_web_agent\cli.py tests\test_factory.py
git commit -m "feat: add reusable visual provider factory"
```

---

### Task 2: Add Agent Visual Provider Bridge

**Files:**
- Create: `Agent/src/web_task_agent/visual_provider.py`
- Modify: `Agent/src/web_task_agent/cli.py`
- Modify: `Agent/src/web_task_agent/evaluation.py`
- Create: `Agent/tests/test_visual_provider.py`

- [ ] **Step 1: Write failing bridge tests**

Create `Agent/tests/test_visual_provider.py`:

```python
import pytest

from web_task_agent.models import BrowserPage
from web_task_agent.visual_provider import (
    VisualProviderConfigurationError,
    build_configured_visual_extractor,
)


@pytest.mark.asyncio
async def test_visual_provider_adapter_converts_external_result_to_visual_fields():
    class FakeExternalJob:
        title = "Real Visual AI Intern"
        company = "Example Vision"
        location = "Remote"
        requirements = "Python, Playwright, Qwen-VL"
        responsibilities = "Extract fields from screenshots"
        skills = ["Python", "Playwright", "Qwen-VL"]
        confidence = 0.91

    class FakeExternalExtractor:
        async def extract(self, url: str):
            class Result:
                success = True
                job = FakeExternalJob()
                error = ""
            return Result()

    adapter = build_configured_visual_extractor(
        provider="qwen-vl",
        extractor_factory=lambda: FakeExternalExtractor(),
    )

    result = await adapter.extract(
        BrowserPage(url="https://example.com/jobs/visual", title="", content="", source="demo")
    )

    assert result.success is True
    assert result.fields is not None
    assert result.fields.title == "Real Visual AI Intern"
    assert result.fields.company == "Example Vision"
    assert result.fields.skills == ["Python", "Playwright", "Qwen-VL"]


def test_visual_provider_builder_raises_clear_error_when_dependency_is_missing(monkeypatch):
    def fake_import(*args, **kwargs):
        raise ModuleNotFoundError("No module named 'visual_web_agent'")

    monkeypatch.setattr("web_task_agent.visual_provider.import_visual_web_agent", fake_import)

    with pytest.raises(VisualProviderConfigurationError) as exc:
        build_configured_visual_extractor(provider="qwen-vl")

    assert "pip install -e" in str(exc.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_visual_provider.py -q
```

Expected: FAIL because `web_task_agent.visual_provider` does not exist yet.

- [ ] **Step 3: Implement the bridge**

Create `Agent/src/web_task_agent/visual_provider.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module

from web_task_agent.models import BrowserPage, JobPosting
from web_task_agent.visual_extractor import VisualExtractionResult, VisualJobFields


class VisualProviderConfigurationError(RuntimeError):
    pass


@dataclass
class QwenVisualExtractorAdapter:
    extractor: object

    async def extract(self, page: BrowserPage) -> VisualExtractionResult:
        result = await self.extractor.extract(page.url)
        if not getattr(result, "success", False) or getattr(result, "job", None) is None:
            return VisualExtractionResult(url=page.url, success=False, fields=None, error=getattr(result, "error", ""))
        job = result.job
        fields = VisualJobFields(
            title=job.title,
            company=job.company,
            location=job.location,
            requirements=job.requirements,
            responsibilities=job.responsibilities,
            skills=list(job.skills),
            confidence=job.confidence,
        )
        return VisualExtractionResult(url=page.url, success=True, fields=fields)


def import_visual_web_agent():
    try:
        return import_module("visual_web_agent.factory")
    except ModuleNotFoundError as exc:
        raise VisualProviderConfigurationError(
            "visual-web-agent is required for --visual-extractor-provider qwen-vl. "
            "Install it with: pip install -e ..\\visual-web-agent"
        ) from exc


def build_configured_visual_extractor(
    *,
    provider: str,
    extractor_factory=None,
    model: str | None = None,
) -> QwenVisualExtractorAdapter:
    if provider != "qwen-vl":
        raise VisualProviderConfigurationError(f"Unsupported visual provider: {provider}")
    if extractor_factory is None:
        factory = import_visual_web_agent()
        external_extractor = factory.build_visual_job_extractor(model=model or "qwen-vl-plus")
    else:
        external_extractor = extractor_factory()
    return QwenVisualExtractorAdapter(extractor=external_extractor)
```

Update `Agent/src/web_task_agent/cli.py`:

```python
from web_task_agent.visual_provider import (
    VisualProviderConfigurationError,
    build_configured_visual_extractor,
)
```

Add parser args:

```python
parser.add_argument(
    "--visual-extractor-provider",
    choices=["qwen-vl"],
    help="Use a configured external visual extractor provider.",
)
parser.add_argument(
    "--visual-extractor-model",
    help="Override the visual provider model, such as qwen-vl-plus.",
)
```

Extend `build_cli_visual_extractor(args)`:

```python
def build_cli_visual_extractor(args: argparse.Namespace):
    if args.visual_extractor_demo:
        return DemoVisualJobExtractor()
    if args.visual_extractor_provider:
        return build_configured_visual_extractor(
            provider=args.visual_extractor_provider,
            model=args.visual_extractor_model,
        )
    return None
```

When provider selection fails, return a clear exit code 2 with the configuration error message.

Update `Agent/src/web_task_agent/evaluation.py` only if the existing constructor path needs the provider factory threaded through more explicitly; otherwise keep the runner unchanged and pass the visual extractor from the CLI path.

- [ ] **Step 4: Run the bridge tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_visual_provider.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src\web_task_agent\visual_provider.py src\web_task_agent\cli.py tests\test_visual_provider.py src\web_task_agent\evaluation.py
git commit -m "feat: add qwen visual provider bridge"
```

---

### Task 3: Thread Real Visual Provider Through Evaluation and Comparison

**Files:**
- Modify: `Agent/src/web_task_agent/cli.py`
- Modify: `Agent/tests/test_scaffold.py`

- [ ] **Step 1: Write failing CLI tests**

Append to `Agent/tests/test_scaffold.py`:

```python
def test_cli_seed_url_can_use_visual_extractor_provider(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeProviderExtractor:
        provider = "qwen-vl"
        model = "qwen-vl-plus"

        async def extract(self, page):
            from web_task_agent.visual_extractor import VisualExtractionResult, VisualJobFields
            return VisualExtractionResult(
                url=page.url,
                success=True,
                fields=VisualJobFields(
                    title="Real Visual AI Intern",
                    company="Example Vision",
                    location="Remote",
                    requirements="Python, Playwright, Qwen-VL",
                    responsibilities="Extract fields from screenshots",
                    skills=["Python", "Playwright", "Qwen-VL"],
                    confidence=0.9,
                ),
            )

    monkeypatch.setattr(
        "web_task_agent.cli.build_configured_visual_extractor",
        lambda *, provider, model=None: FakeProviderExtractor(),
    )

    assert (
        main(
            [
                "--seed-url",
                "https://example.com/jobs/visual-ai-intern",
                "--target-count",
                "1",
                "--demo",
                "--visual-extractor-provider",
                "qwen-vl",
                "--json-output",
                "outputs/visual-provider.json",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Visual extractor provider: qwen-vl" in captured.out
    payload = json.loads((tmp_path / "outputs" / "visual-provider.json").read_text(encoding="utf-8"))
    assert payload["metadata"]["extractor_mode"] == "visual-provider"
    assert payload["metadata"]["visual_provider"] == "qwen-vl"
    assert payload["jobs"][0]["title"] == "Real Visual AI Intern"


def test_cli_compare_extractor_can_include_visual_provider(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeProviderExtractor:
        provider = "qwen-vl"
        model = "qwen-vl-plus"

        async def extract(self, page):
            from web_task_agent.visual_extractor import VisualExtractionResult, VisualJobFields
            return VisualExtractionResult(
                url=page.url,
                success=True,
                fields=VisualJobFields(
                    title="Real Visual AI Intern",
                    company="Example Vision",
                    location="Remote",
                    requirements="Python, Playwright, Qwen-VL",
                    responsibilities="Extract fields from screenshots",
                    skills=["Python", "Playwright", "Qwen-VL"],
                    confidence=0.9,
                ),
            )

    monkeypatch.setattr(
        "web_task_agent.cli.build_configured_visual_extractor",
        lambda *, provider, model=None: FakeProviderExtractor(),
    )

    assert (
        main(
            [
                "--compare-llm-extractor",
                "--seed-url",
                "https://example.com/jobs/visual-ai-intern",
                "--visual-extractor-provider",
                "qwen-vl",
                "--json-output",
                "evaluations/visual-provider-comparison.json",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "qwen-vl: 1/1" in captured.out
    payload = json.loads(
        (tmp_path / "evaluations" / "visual-provider-comparison.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["extractors"]["qwen-vl"]["completed_tasks"] == 1
    report = (tmp_path / payload["report_path"]).read_text(encoding="utf-8")
    assert "| qwen-vl | 1 | 1 | 1.00 |" in report
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_seed_url_can_use_visual_extractor_provider tests\test_scaffold.py::test_cli_compare_extractor_can_include_visual_provider -q
```

Expected: FAIL because the provider flag and output do not exist yet.

- [ ] **Step 3: Implement the CLI wiring**

In `Agent/src/web_task_agent/cli.py`:

```python
parser.add_argument(
    "--visual-extractor-provider",
    choices=["qwen-vl"],
    help="Use a configured external visual extractor provider.",
)
parser.add_argument(
    "--visual-extractor-model",
    help="Override the visual provider model, such as qwen-vl-plus.",
)
```

Extend the main workflow path:

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

Record provider metadata after a successful run:

```python
if args.visual_extractor_provider:
    print(f"Visual extractor provider: {args.visual_extractor_provider}")
    state.metadata["extractor_mode"] = "visual-provider"
    state.metadata["visual_provider"] = args.visual_extractor_provider
    state.metadata["visual_model"] = getattr(visual_extractor, "model", args.visual_extractor_model or "")
```

Extend `run_llm_extractor_comparison()` with a third extractor result:

```python
if args.visual_extractor_provider:
    visual_provider = await EvaluationRunner(
        args.evaluation_dir,
        browser_factory=browser_factory,
        extractor_factory=lambda task: PageExtractor(),
        visual_extractor_factory=lambda task: build_cli_visual_extractor(args),
    ).run(tasks=tasks)
    extractors[args.visual_extractor_provider] = visual_provider.model_dump(mode="json")
```

Update `_run()` output to print the provider row:

```python
if args.visual_extractor_provider:
    provider_result = result[args.visual_extractor_provider]
    print(
        f"{args.visual_extractor_provider}: "
        f"{provider_result['completed_tasks']}/{provider_result['total_tasks']}"
    )
```

- [ ] **Step 4: Run the CLI tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_seed_url_can_use_visual_extractor_provider tests\test_scaffold.py::test_cli_compare_extractor_can_include_visual_provider -q
```

Expected: PASS.

- [ ] **Step 5: Run the evaluation smoke tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_demo_mode_can_use_llm_extractor_demo tests\test_scaffold.py::test_cli_compare_llm_extractor_writes_comparison_json tests\test_scaffold.py::test_cli_evaluate_mode_writes_report -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add src\web_task_agent\cli.py tests\test_scaffold.py
git commit -m "feat: wire visual provider through cli and comparison"
```

---

### Task 4: Update Documentation and Work Log

**Files:**
- Modify: `Agent/README.md`
- Modify: `visual-web-agent/README.md`
- Create: `Agent/docs/work-log/2026-06-29-visual-provider-bridge.md`

- [ ] **Step 1: Write documentation tests or verification checks**

This task is documentation-only, so verify it with the actual commands the README will advertise:

```powershell
.\.venv\Scripts\python.exe -m pip install -e "..\visual-web-agent"
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/visual-ai-intern" --demo --target-count 1 --visual-extractor-provider qwen-vl --json-output outputs\visual-provider.json
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --seed-url "https://example.com/jobs/visual-ai-intern" --visual-extractor-provider qwen-vl --json-output evaluations\visual-provider-comparison.json
.\.venv\Scripts\visual-web-agent.exe --url "https://example.com/jobs/visual-ai-intern" --demo --extract-job --json-output outputs\visual-job.json
```

- [ ] **Step 2: Update the READMEs**

Add a short section to `Agent/README.md`:

```markdown
### Real visual provider

The real Qwen-VL visual provider lives in the sibling `visual-web-agent` package. Install it into the same virtualenv first:

```powershell
python -m pip install -e "..\\visual-web-agent"
```

Then run:

```powershell
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/visual-ai-intern" --demo --target-count 1 --visual-extractor-provider qwen-vl --json-output outputs\visual-provider.json
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --seed-url "https://example.com/jobs/visual-ai-intern" --visual-extractor-provider qwen-vl --json-output evaluations\visual-provider-comparison.json
```
```

Add a short section to `visual-web-agent/README.md`:

```markdown
### Reusable factory

Use `visual_web_agent.factory.build_visual_job_extractor()` when another package needs the screenshot + VLM pipeline without calling the CLI.
```

Create `Agent/docs/work-log/2026-06-29-visual-provider-bridge.md`:

```markdown
# 本轮工作：Real Visual Provider Bridge

## 目标

把 `visual-web-agent` 的真实 Qwen-VL 截图理解链路桥接到 `Agent`，让 seed URL workflow 和 comparison 进入真实视觉 provider 阶段。

## 实施范围

- `visual-web-agent` 提供可复用的 extractor factory。
- `Agent` 增加 `--visual-extractor-provider qwen-vl`。
- `Agent` 的 visual provider 输出继续落到 `JobPosting`、report、dashboard、evaluation。
- 文档补齐本地 sibling-package 安装方式。

## 验证命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_factory.py tests\test_extractor.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_visual_provider.py tests\test_scaffold.py -q
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/visual-ai-intern" --demo --target-count 1 --visual-extractor-provider qwen-vl --json-output outputs\visual-provider.json
```

## 当前边界

- 真实 provider 仍然通过 `visual-web-agent` 负责截图和 VLM 调用。
- `Agent` 只负责桥接、验证、匹配、报告和评测。
- 如果 sibling package 没装，CLI 应返回清晰的配置错误，不要静默降级。
```

- [ ] **Step 3: Run doc sanity checks**

Run:

```powershell
.\.venv\Scripts\web-task-agent.exe --print-demo-script
```

Expected: exit code 0 and the existing demo script text prints normally.

- [ ] **Step 4: Commit**

Run:

```powershell
git add README.md docs\work-log\2026-06-29-visual-provider-bridge.md
git commit -m "docs: describe the real visual provider bridge"
```

---

### Task 5: Final Verification

**Files:**
- No new files.

- [ ] **Step 1: Install the sibling package into the Agent venv**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install -e "..\visual-web-agent"
```

Expected: editable install succeeds and `visual_web_agent` imports from the sibling repo.

- [ ] **Step 2: Run the full test suite in both repos**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest
pushd ..\visual-web-agent
.\.venv\Scripts\python.exe -m pytest
popd
```

Expected: both test suites pass.

- [ ] **Step 3: Run the demo and provider smoke commands**

Run:

```powershell
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/visual-ai-intern" --demo --target-count 1 --visual-extractor-provider qwen-vl --json-output outputs\visual-provider.json
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --seed-url "https://example.com/jobs/visual-ai-intern" --visual-extractor-provider qwen-vl --json-output evaluations\visual-provider-comparison.json
.\.venv\Scripts\visual-web-agent.exe --url "https://example.com/jobs/visual-ai-intern" --extract-job --json-output outputs\visual-job.json
```

Expected:

```text
Visual extractor provider: qwen-vl
Report written to:
Valid jobs: 1
```

and

```text
baseline:
llm-demo:
qwen-vl:
Comparison report written to:
Comparison JSON written to:
```

- [ ] **Step 4: Inspect the generated JSON and report artifacts**

Run:

```powershell
Get-Content -Raw outputs\visual-provider.json | ConvertFrom-Json | Select-Object -ExpandProperty metadata
Get-Content -Raw evaluations\visual-provider-comparison.json | ConvertFrom-Json | Select-Object -ExpandProperty extractors
```

Expected metadata includes:

```text
extractor_mode : visual-provider
visual_provider : qwen-vl
```

and comparison output includes a `qwen-vl` extractor row.

- [ ] **Step 5: Check git status**

Run:

```powershell
git status --short
```

Expected: only intentional edited files remain, or the tree is clean after commits.

