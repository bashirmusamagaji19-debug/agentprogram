# Benchmark Explanation Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Chinese benchmark explanation layer that turns `benchmark-v2.json`, `benchmark-v2.md`, and `benchmark-v2.html` from raw provider results into interview-ready conclusions, failure analysis, and speaking notes.

**Architecture:** Keep `web_task_agent.benchmark` responsible for benchmark execution and raw matrix artifacts. Add a focused `benchmark_explainer` module that derives deterministic insights from `BenchmarkMatrixResult` without calling external LLMs, so explanations are repeatable and testable. Wire the explainer into the CLI behind `--benchmark-explain`, then surface the same short insights in the HTML dashboard and documentation.

**Tech Stack:** Python 3.11+, Pydantic, pytest, existing `BenchmarkMatrixResult`, Markdown artifacts, existing `HtmlDashboard`, existing CLI benchmark v2 flow.

---

## File Structure

- Create: `src/web_task_agent/benchmark_explainer.py`
  - Owns explanation data models, insight heuristics, Chinese Markdown rendering, and artifact writing.
- Create: `tests/test_benchmark_explainer.py`
  - Unit tests for insight generation and Chinese explanation Markdown.
- Modify: `src/web_task_agent/benchmark.py`
  - Optionally imports no explainer code; keep this module as raw benchmark matrix/report owner.
- Modify: `src/web_task_agent/cli.py`
  - Adds `--benchmark-explain`.
  - Calls the explainer after `write_benchmark_artifacts()` when the flag is enabled.
  - Prints the generated explanation path.
  - Adds the flag to `--print-demo-script`.
- Modify: `src/web_task_agent/dashboard.py`
  - Adds a small explanation summary section to `render_benchmark_summary()` when insights are provided.
- Modify: `tests/test_scaffold.py`
  - Adds CLI smoke coverage for `--benchmark-explain` and demo script output.
- Modify: `tests/test_dashboard.py`
  - Adds dashboard coverage for explanation summary rendering.
- Modify: `README.md`
  - Documents the explain flag and generated artifact.
- Modify: `docs/interview-benchmark-story.md`
  - Adds the polished Chinese interview story using V2 benchmark language.
- Create: `docs/work-log/2026-06-29-benchmark-explanation-layer.md`
  - Records what changed, how to run it, and how to use it in an interview.

---

### Task 1: Add Benchmark Insight Model and Heuristics

**Files:**
- Create: `src/web_task_agent/benchmark_explainer.py`
- Create: `tests/test_benchmark_explainer.py`

- [ ] **Step 1: Write failing tests for deterministic insight generation**

Create `tests/test_benchmark_explainer.py`:

```python
from web_task_agent.benchmark import (
    BenchmarkCase,
    BenchmarkMatrixResult,
    BenchmarkProviderResult,
)
from web_task_agent.benchmark_explainer import (
    BenchmarkInsight,
    generate_benchmark_insights,
)


def _case(case_id: str = "anthropic-claude-evangelist") -> BenchmarkCase:
    return BenchmarkCase(
        case_id=case_id,
        company="Anthropic",
        ats="greenhouse",
        role_family="ai-applications",
        keyword="Applied AI Claude Evangelist",
        location="San Francisco, CA",
        skills=["AI", "customer"],
        url="https://job-boards.greenhouse.io/anthropic/jobs/5116927008",
        expected_signal="AI application and Claude product role",
    )


def _provider(
    name: str,
    completed: int,
    total: int = 2,
    failures: dict[str, int] | None = None,
) -> BenchmarkProviderResult:
    return BenchmarkProviderResult(
        provider=name,
        total_tasks=total,
        completed_tasks=completed,
        success_rate=completed / total,
        total_valid_jobs=completed,
        average_pages_visited=1.0,
        failure_counts=failures or {},
        elapsed_seconds=0.1,
        report_path=f"evaluations/{name}/evaluation-report.md",
    )


def test_generate_benchmark_insights_summarizes_best_provider_and_gap():
    result = BenchmarkMatrixResult(
        cases=[_case(), _case("anthropic-api-platform-tpm")],
        providers=[
            _provider("baseline", 1, failures={"verification_filtered": 1}),
            _provider("llm-demo", 1, failures={"verification_filtered": 1}),
            _provider("deepseek", 2),
        ],
    )

    insight = generate_benchmark_insights(result)

    assert isinstance(insight, BenchmarkInsight)
    assert insight.best_provider == "deepseek"
    assert "deepseek" in insight.one_sentence
    assert "2/2" in insight.one_sentence
    assert any(item.provider == "baseline" for item in insight.provider_notes)
    assert any(item.failure_category == "verification_filtered" for item in insight.failure_notes)
    assert insight.interview_pitch_60s.startswith("我把这个项目")


def test_generate_benchmark_insights_handles_empty_matrix():
    insight = generate_benchmark_insights(BenchmarkMatrixResult(cases=[], providers=[]))

    assert insight.best_provider == ""
    assert "还没有可解释的 benchmark 结果" in insight.one_sentence
    assert insight.provider_notes == []
    assert insight.failure_notes == []
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
cd C:\Users\13993\Desktop\大模型学习\Agent
.\.venv\Scripts\python.exe -m pytest tests\test_benchmark_explainer.py -q
```

Expected: FAIL because `web_task_agent.benchmark_explainer` does not exist.

- [ ] **Step 3: Implement the insight models and heuristics**

Create `src/web_task_agent/benchmark_explainer.py`:

```python
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from web_task_agent.benchmark import BenchmarkMatrixResult


class ProviderInsight(BaseModel):
    provider: str
    completed: str
    success_rate: float
    note: str


class FailureInsight(BaseModel):
    failure_category: str
    count: int
    explanation: str


class BenchmarkInsight(BaseModel):
    best_provider: str = ""
    one_sentence: str
    provider_notes: list[ProviderInsight] = Field(default_factory=list)
    failure_notes: list[FailureInsight] = Field(default_factory=list)
    visual_provider_value: str
    not_prompt_demo_reason: str
    engineering_judgement: str
    interview_pitch_60s: str
    interview_pitch_3min: str


FAILURE_EXPLANATIONS = {
    "verification_filtered": "页面能被读取和抽取，但 verifier 认为结果没有达到岗位有效性要求；这通常说明抽取到了弱相关内容，或者岗位信号不足。",
    "browser_error": "页面打开失败，问题在网页可访问性、网络、反爬或浏览器加载层，而不是抽取器本身。",
    "empty_result": "工作流跑完但没有得到候选岗位，通常要检查页面结构、选择器、文本抽取和 visual fallback。",
    "extractor_error": "页面加载后抽取器报错，优先检查结构化解析、LLM provider 返回格式和异常处理。",
}


def generate_benchmark_insights(result: BenchmarkMatrixResult) -> BenchmarkInsight:
    if not result.providers:
        return BenchmarkInsight(
            one_sentence="还没有可解释的 benchmark 结果；请先运行 --benchmark-v2 生成 provider matrix。",
            visual_provider_value="暂无 visual provider 数据。",
            not_prompt_demo_reason="暂无 benchmark 数据，无法证明它不是一次性 prompt demo。",
            engineering_judgement="先补齐 benchmark 结果，再讨论 provider 差异和失败归因。",
            interview_pitch_60s="我会先运行 benchmark，再用统一指标解释结果。",
            interview_pitch_3min="目前还没有 provider matrix，因此下一步是生成 benchmark-v2.json、benchmark-v2.md 和 dashboard，再做解释层。",
        )

    best = max(result.providers, key=lambda item: (item.success_rate, item.total_valid_jobs))
    total_cases = len(result.cases)
    one_sentence = (
        f"这轮 Real Site Benchmark V2 覆盖 {total_cases} 个真实岗位样本，"
        f"当前最好的 provider 是 {best.provider}，完成 {best.completed_tasks}/{best.total_tasks}，"
        f"success_rate={best.success_rate:.2f}。"
    )

    provider_notes = [_provider_note(provider, best.provider) for provider in result.providers]
    failure_notes = _failure_notes(result)

    has_visual = any(provider.provider == "qwen-vl" for provider in result.providers)
    visual_provider_value = (
        "visual provider 的价值在于补足纯文本抽取看不到或结构不稳定的页面信号；它被放进同一张 provider matrix，而不是单独讲 demo。"
        if has_visual
        else "当前矩阵还没有 visual provider；下一次可以加入 qwen-vl，用同一批真实页面验证视觉抽取是否带来增益。"
    )
    not_prompt_demo_reason = (
        "它不是 prompt demo，因为样本、workflow、verifier、失败分类和 provider 输出都被固定成可复跑的评测矩阵。"
    )
    engineering_judgement = (
        "工程判断重点不是宣称某个 provider 永远最好，而是把成功率、失败类型和页面漂移显式记录下来，让系统能被复盘。"
    )

    interview_pitch_60s = (
        "我把这个项目从能跑通的 Web Agent 做成了可验证的 benchmark。"
        f"同一批真实招聘页面会同时跑 baseline、LLM 和 visual provider，"
        f"然后输出 success rate、valid jobs 和 failure_counts。"
        f"这轮最好的结果是 {best.provider} {best.completed_tasks}/{best.total_tasks}，"
        "失败项也会说明是 verifier 过滤、页面加载还是抽取失败，所以面试时我能讲清楚边界，而不是只展示一次成功。"
    )
    interview_pitch_3min = (
        "这个 benchmark 的核心设计是把真实网页的不稳定性纳入评测，而不是假装它不存在。"
        "我先固定真实岗位样本目录，每个 case 都有公司、ATS、岗位族、URL 和 expected signal；"
        "再让不同 provider 走同一个 EvaluationRunner、同一个 verifier、同一套 failure category。"
        f"输出结果显示当前 best provider 是 {best.provider}，但我更关心的是为什么它好、哪里失败、失败是否来自页面漂移。"
        "这样项目就从 prompt demo 变成了可复跑、可解释、可回归的工程系统。"
    )

    return BenchmarkInsight(
        best_provider=best.provider,
        one_sentence=one_sentence,
        provider_notes=provider_notes,
        failure_notes=failure_notes,
        visual_provider_value=visual_provider_value,
        not_prompt_demo_reason=not_prompt_demo_reason,
        engineering_judgement=engineering_judgement,
        interview_pitch_60s=interview_pitch_60s,
        interview_pitch_3min=interview_pitch_3min,
    )


def _provider_note(provider, best_provider: str) -> ProviderInsight:
    completed = f"{provider.completed_tasks}/{provider.total_tasks}"
    if provider.provider == best_provider:
        note = "当前矩阵中的最优 provider，可以作为本轮结果的主结论。"
    elif provider.success_rate == 0:
        note = "当前没有完成样本，优先检查配置、页面访问或抽取链路。"
    else:
        note = "有部分样本完成，但仍需要结合 failure_counts 判断短板。"
    return ProviderInsight(
        provider=provider.provider,
        completed=completed,
        success_rate=provider.success_rate,
        note=note,
    )


def _failure_notes(result: BenchmarkMatrixResult) -> list[FailureInsight]:
    totals: dict[str, int] = {}
    for provider in result.providers:
        for category, count in provider.failure_counts.items():
            totals[category] = totals.get(category, 0) + count
    return [
        FailureInsight(
            failure_category=category,
            count=count,
            explanation=FAILURE_EXPLANATIONS.get(
                category,
                "未归类失败，需要回到 provider 的 evaluation report 查看 failure_details。",
            ),
        )
        for category, count in sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    ]
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_benchmark_explainer.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src\web_task_agent\benchmark_explainer.py tests\test_benchmark_explainer.py
git commit -m "feat: add benchmark explanation insights"
```

---

### Task 2: Render Chinese Explanation Markdown Artifact

**Files:**
- Modify: `src/web_task_agent/benchmark_explainer.py`
- Modify: `tests/test_benchmark_explainer.py`

- [ ] **Step 1: Write failing tests for the Markdown renderer**

Append to `tests/test_benchmark_explainer.py`:

```python
from web_task_agent.benchmark_explainer import (
    render_benchmark_explanation_markdown,
    write_benchmark_explanation_artifact,
)


def test_render_benchmark_explanation_markdown_contains_interview_sections():
    result = BenchmarkMatrixResult(
        cases=[_case(), _case("anthropic-api-platform-tpm")],
        providers=[
            _provider("baseline", 1, failures={"verification_filtered": 1}),
            _provider("deepseek", 2),
        ],
    )
    insight = generate_benchmark_insights(result)

    markdown = render_benchmark_explanation_markdown(result, insight)

    assert "# Benchmark V2 结果解释" in markdown
    assert "## 一句话结论" in markdown
    assert "## Provider 矩阵怎么读" in markdown
    assert "## 失败原因说明" in markdown
    assert "## 为什么这不是 prompt demo" in markdown
    assert "## 面试 60 秒讲法" in markdown
    assert "deepseek" in markdown
    assert "verification_filtered" in markdown


def test_write_benchmark_explanation_artifact(tmp_path):
    result = BenchmarkMatrixResult(cases=[_case()], providers=[_provider("deepseek", 1, total=1)])
    insight = generate_benchmark_insights(result)

    path = write_benchmark_explanation_artifact(
        result=result,
        insight=insight,
        output_dir=tmp_path,
    )

    assert path.name == "benchmark-v2-explained.md"
    assert "Benchmark V2 结果解释" in path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_benchmark_explainer.py -q
```

Expected: FAIL because renderer functions do not exist.

- [ ] **Step 3: Implement the renderer and writer**

Append to `src/web_task_agent/benchmark_explainer.py`:

```python
def render_benchmark_explanation_markdown(
    result: BenchmarkMatrixResult,
    insight: BenchmarkInsight,
) -> str:
    provider_lines = [
        "| Provider | 完成情况 | Success Rate | 解读 |",
        "|---|---:|---:|---|",
    ]
    for note in insight.provider_notes:
        provider_lines.append(
            f"| {note.provider} | {note.completed} | {note.success_rate:.2f} | {note.note} |"
        )

    failure_lines = [
        "| Failure Category | Count | 说明 |",
        "|---|---:|---|",
    ]
    if insight.failure_notes:
        for note in insight.failure_notes:
            failure_lines.append(
                f"| {note.failure_category} | {note.count} | {note.explanation} |"
            )
    else:
        failure_lines.append("| - | 0 | 本轮没有记录失败分类。 |")

    case_lines = [
        "| Case ID | Company | Role Family | Expected Signal |",
        "|---|---|---|---|",
    ]
    for case in result.cases:
        case_lines.append(
            f"| {case.case_id} | {case.company} | {case.role_family} | {case.expected_signal} |"
        )

    lines = [
        "# Benchmark V2 结果解释",
        "",
        "## 一句话结论",
        "",
        insight.one_sentence,
        "",
        "## Provider 矩阵怎么读",
        "",
        *provider_lines,
        "",
        "## 失败原因说明",
        "",
        *failure_lines,
        "",
        "## 为什么 visual provider 有价值",
        "",
        insight.visual_provider_value,
        "",
        "## 为什么这不是 prompt demo",
        "",
        insight.not_prompt_demo_reason,
        "",
        "## 工程判断",
        "",
        insight.engineering_judgement,
        "",
        "## 面试 60 秒讲法",
        "",
        insight.interview_pitch_60s,
        "",
        "## 面试 3 分钟讲法",
        "",
        insight.interview_pitch_3min,
        "",
        "## 样本目录速览",
        "",
        *case_lines,
        "",
    ]
    return "\n".join(lines)


def write_benchmark_explanation_artifact(
    *,
    result: BenchmarkMatrixResult,
    insight: BenchmarkInsight,
    output_dir: str | Path,
) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "benchmark-v2-explained.md"
    path.write_text(
        render_benchmark_explanation_markdown(result, insight),
        encoding="utf-8",
    )
    return path
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_benchmark_explainer.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src\web_task_agent\benchmark_explainer.py tests\test_benchmark_explainer.py
git commit -m "feat: render benchmark explanation markdown"
```

---

### Task 3: Add CLI Flag and Demo Script Wiring

**Files:**
- Modify: `src/web_task_agent/cli.py`
- Modify: `tests/test_scaffold.py`

- [ ] **Step 1: Add CLI smoke test for `--benchmark-explain`**

Append to `tests/test_scaffold.py`:

```python
def test_cli_benchmark_v2_can_write_explanation(
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
                    completed_tasks=1,
                    success_rate=1.0,
                    total_valid_jobs=1,
                    average_pages_visited=1.0,
                    failure_counts={},
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
            "baseline,deepseek",
            "--benchmark-limit",
            "1",
            "--benchmark-explain",
        ]
    )

    assert exit_code == 0
    assert (tmp_path / "evaluations" / "benchmark-v2-explained.md").exists()
    captured = capsys.readouterr()
    assert "Benchmark explanation written to:" in captured.out
```

Update the existing `test_cli_prints_demo_script` assertion:

```python
    assert "--benchmark-explain" in captured.out
```

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py -k "benchmark_v2_can_write_explanation or demo_script" -q
```

Expected: FAIL because the flag and CLI wiring do not exist.

- [ ] **Step 3: Add imports and parser flag**

In `src/web_task_agent/cli.py`, add imports near benchmark imports:

```python
from web_task_agent.benchmark_explainer import (
    generate_benchmark_insights,
    write_benchmark_explanation_artifact,
)
```

Add to `build_parser()` near the benchmark flags:

```python
    parser.add_argument(
        "--benchmark-explain",
        action="store_true",
        help="Write a Chinese explanation artifact for benchmark v2 results.",
    )
```

- [ ] **Step 4: Wire explanation writing in the benchmark branch**

In `_run(args)`, inside `if args.benchmark_v2:` after `result = await run_cli_benchmark_v2(...)` and before printing provider rows, add:

```python
        if args.benchmark_explain:
            insight = generate_benchmark_insights(result)
            explanation_path = write_benchmark_explanation_artifact(
                result=result,
                insight=insight,
                output_dir=args.evaluation_dir,
            )
            print(f"Benchmark explanation written to: {explanation_path}")
```

- [ ] **Step 5: Update `print_demo_script()` benchmark command**

Change the benchmark command in `print_demo_script()` to:

```python
        (
            r".\.venv\Scripts\web-task-agent.exe --benchmark-v2 "
            r"--benchmark-providers baseline,llm-demo,deepseek "
            r"--benchmark-limit 8 --benchmark-dashboard --benchmark-explain"
        ),
```

- [ ] **Step 6: Run focused CLI tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py -k "benchmark_v2_can_write_explanation or demo_script" -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add src\web_task_agent\cli.py tests\test_scaffold.py
git commit -m "feat: add benchmark explanation cli flag"
```

---

### Task 4: Add Dashboard Explanation Summary

**Files:**
- Modify: `src/web_task_agent/dashboard.py`
- Modify: `src/web_task_agent/cli.py`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Write dashboard test for optional insight rendering**

Append to `tests/test_dashboard.py`:

```python
from web_task_agent.benchmark_explainer import generate_benchmark_insights


def test_dashboard_renders_benchmark_explanation_summary():
    result = BenchmarkMatrixResult(
        cases=build_real_site_benchmark_v2_cases()[:1],
        providers=[
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
    insight = generate_benchmark_insights(result)

    html = HtmlDashboard().render_benchmark_summary(result, insight=insight)

    assert "结果解释" in html
    assert "一句话结论" in html
    assert "deepseek" in html
    assert "为什么这不是 prompt demo" in html
```

- [ ] **Step 2: Run test and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_dashboard.py::test_dashboard_renders_benchmark_explanation_summary -q
```

Expected: FAIL because `render_benchmark_summary()` does not accept `insight`.

- [ ] **Step 3: Update `HtmlDashboard.render_benchmark_summary()` signature and section**

In `src/web_task_agent/dashboard.py`, change:

```python
    def render_benchmark_summary(self, result) -> str:
```

to:

```python
    def render_benchmark_summary(self, result, *, insight=None) -> str:
```

Before the provider matrix heading in the returned HTML, compute:

```python
        explanation_section = ""
        if insight is not None:
            explanation_section = f"""
    <h2>结果解释</h2>
    <section>
      <h3>一句话结论</h3>
      <p>{escape(insight.one_sentence)}</p>
      <h3>为什么这不是 prompt demo</h3>
      <p>{escape(insight.not_prompt_demo_reason)}</p>
      <h3>工程判断</h3>
      <p>{escape(insight.engineering_judgement)}</p>
    </section>
"""
```

Then insert `{explanation_section}` in the HTML after the metric section:

```python
    <section class="metrics">
      ...
    </section>
    {explanation_section}
    <h2>Provider Matrix</h2>
```

- [ ] **Step 4: Pass insight from CLI when dashboard and explain are both enabled**

In `run_cli_benchmark_v2()` keep writing the raw dashboard as-is. In `_run(args)`, when `args.benchmark_v2` finishes, do not rewrite the dashboard here unless needed. Instead, update `run_cli_benchmark_v2()` so it can accept optional `explain: bool = False`:

```python
async def run_cli_benchmark_v2(
    args: argparse.Namespace,
    *,
    providers: list[str],
    explain: bool = False,
):
```

Inside `run_cli_benchmark_v2()` after `result` is created:

```python
    insight = generate_benchmark_insights(result) if explain else None
```

Use it when writing the dashboard:

```python
        dashboard_path.write_text(
            HtmlDashboard(args.dashboard_dir).render_benchmark_summary(
                result,
                insight=insight,
            ),
            encoding="utf-8",
        )
```

Change the caller in `_run(args)`:

```python
            result = await run_cli_benchmark_v2(
                args,
                providers=providers,
                explain=args.benchmark_explain,
            )
```

Keep the separate Markdown explanation writing in `_run(args)` from Task 3.

- [ ] **Step 5: Run dashboard tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_dashboard.py tests\test_scaffold.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add src\web_task_agent\dashboard.py src\web_task_agent\cli.py tests\test_dashboard.py
git commit -m "feat: show benchmark explanations in dashboard"
```

---

### Task 5: Update Docs and Work Log

**Files:**
- Modify: `README.md`
- Modify: `docs/interview-benchmark-story.md`
- Create: `docs/work-log/2026-06-29-benchmark-explanation-layer.md`

- [ ] **Step 1: Update README benchmark section**

In `README.md`, find the Real Site Benchmark V2 section and update the command:

```markdown
.\.venv\Scripts\web-task-agent.exe --benchmark-v2 --benchmark-providers baseline,llm-demo,deepseek --benchmark-limit 8 --benchmark-dashboard --benchmark-explain
```

Add this output bullet:

```markdown
- `evaluations/benchmark-v2-explained.md`: Chinese explanation layer with the one-sentence conclusion, provider interpretation, failure analysis, and interview pitches.
```

Add this interpretation note:

```markdown
Use `benchmark-v2-explained.md` as the interview-facing artifact. `benchmark-v2.json` is the source of truth, `benchmark-v2.md` is the raw matrix, and the explained report is the narrative layer that answers "what does this result prove?"
```

- [ ] **Step 2: Replace the interview story benchmark paragraph with clean Chinese**

In `docs/interview-benchmark-story.md`, replace the V2 explanation section with:

```markdown
## Real Site Benchmark V2 讲法

这一阶段我把真实站点评测从一次性的 comparison 升级成 provider matrix。每个样本不只有 URL，还有公司、ATS 类型、岗位族、期望信号和技能标签；每个 provider 都跑同一批样本，输出完成率、有效岗位数、失败分类和耗时。

面试时重点不是说某个 provider 永远最好，而是说明我如何设计可复现评测：固定样本目录、统一 workflow、统一 verifier、统一失败分类，再把 rule、LLM、visual provider 放在同一张矩阵里比较。真实页面可能变化，所以系统把 HTTP、空页面、抽取失败、verifier 过滤都记录下来。这比只展示一次成功 demo 更能体现工程判断。

新增的 `benchmark-v2-explained.md` 是讲述层：它把矩阵翻译成一句话结论、provider 对比、失败原因说明、visual provider 价值，以及 60 秒和 3 分钟面试讲法。
```

- [ ] **Step 3: Create the work log**

Create `docs/work-log/2026-06-29-benchmark-explanation-layer.md`:

```markdown
# 本轮工作：Benchmark 结果解释层

## 完成了什么

- 新增 benchmark insight 生成逻辑，把 provider matrix 翻译成中文解释。
- 新增 `evaluations/benchmark-v2-explained.md`，包含一句话结论、provider 矩阵解读、失败原因说明、visual provider 价值和面试讲法。
- 新增 `--benchmark-explain`，让 benchmark v2 可以一键生成 raw matrix、JSON、dashboard 和解释报告。
- dashboard 在开启 explain 时展示短版解释，便于演示时快速讲清楚结果。

## 你要理解什么

这一层不改变抽取器能力，也不引入新的 LLM 判断。它只基于已有的 `BenchmarkMatrixResult` 做确定性总结，所以结果可测试、可复跑、可审查。面试时可以把它理解为 raw benchmark 和人类叙事之间的翻译层。

## 你现在应该做什么

运行：

```powershell
.\.venv\Scripts\web-task-agent.exe --benchmark-v2 --benchmark-providers baseline,llm-demo,deepseek --benchmark-limit 8 --benchmark-dashboard --benchmark-explain
```

然后按顺序查看：

- `evaluations/benchmark-v2.json`
- `evaluations/benchmark-v2.md`
- `evaluations/benchmark-v2-explained.md`
- `dashboards/benchmark-v2.html`
```

- [ ] **Step 4: Run documentation-sensitive tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_prints_demo_script -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add README.md docs\interview-benchmark-story.md docs\work-log\2026-06-29-benchmark-explanation-layer.md
git commit -m "docs: explain benchmark v2 interview story"
```

---

### Task 6: Final Verification

**Files:**
- No new implementation files.

- [ ] **Step 1: Run full test suite**

Run:

```powershell
cd C:\Users\13993\Desktop\大模型学习\Agent
.\.venv\Scripts\python.exe -m pytest
```

Expected: all tests pass.

- [ ] **Step 2: Run benchmark explanation without external provider keys**

Run:

```powershell
.\.venv\Scripts\web-task-agent.exe --benchmark-v2 --benchmark-providers baseline,llm-demo --benchmark-limit 2 --benchmark-dashboard --benchmark-explain
```

Expected stdout includes:

```text
Benchmark Markdown written to:
Benchmark JSON written to:
Benchmark dashboard written to:
Benchmark explanation written to:
Real site benchmark v2
baseline:
llm-demo:
```

Expected files:

```text
evaluations/benchmark-v2.json
evaluations/benchmark-v2.md
evaluations/benchmark-v2-explained.md
dashboards/benchmark-v2.html
```

- [ ] **Step 3: Inspect explanation artifact**

Run:

```powershell
Get-Content -Raw evaluations\benchmark-v2-explained.md
```

Expected content includes:

```text
# Benchmark V2 结果解释
## 一句话结论
## Provider 矩阵怎么读
## 失败原因说明
## 为什么这不是 prompt demo
## 面试 60 秒讲法
```

- [ ] **Step 4: Run configured provider check if keys are available**

Run only if `DEEPSEEK_API_KEY` is configured:

```powershell
.\.venv\Scripts\web-task-agent.exe --benchmark-v2 --benchmark-providers baseline,llm-demo,deepseek --benchmark-limit 2 --benchmark-dashboard --benchmark-explain
```

Expected: stdout includes `deepseek:` and the explanation artifact names `deepseek` as best provider if it has the highest `(success_rate, total_valid_jobs)` pair.

- [ ] **Step 5: Check dashboard contains explanation text**

Run:

```powershell
Get-Content -Raw dashboards\benchmark-v2.html | Select-String -Pattern "结果解释|一句话结论|prompt demo"
```

Expected: matches all three patterns when `--benchmark-dashboard --benchmark-explain` was used.

- [ ] **Step 6: Check git status**

Run:

```powershell
git status --short
```

Expected: clean worktree after commits.

---

## Self-Review

- Spec coverage: The plan covers insight generation, Chinese explanation Markdown, CLI flag, dashboard summary, README/story/work-log updates, and verification.
- Placeholder scan: No `TBD`, `TODO`, "implement later", or vague "add tests" placeholders remain.
- Type consistency: `BenchmarkInsight`, `ProviderInsight`, `FailureInsight`, `generate_benchmark_insights()`, `render_benchmark_explanation_markdown()`, and `write_benchmark_explanation_artifact()` are defined before use.
- Scope check: This plan does not change extraction, verification, matching, provider configuration, or visual-web-agent integration. It only explains the benchmark matrix already produced by V2.
