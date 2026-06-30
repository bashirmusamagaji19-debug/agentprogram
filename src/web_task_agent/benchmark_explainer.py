"""Deterministic Chinese explanation layer for benchmark v2 results.

Translates a ``BenchmarkMatrixResult`` into interview-ready insights
without calling any LLM — all heuristics are deterministic and testable.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from web_task_agent.benchmark import BenchmarkMatrixResult

# ── Failure explanations (all 7 categories from EvaluationRunner) ────

FAILURE_EXPLANATIONS: dict[str, str] = {
    "verification_filtered": (
        "页面能被读取和抽取，但 verifier 认为结果没有达到岗位有效性要求；"
        "这通常说明抽取到了弱相关内容，或者岗位信号不足。"
    ),
    "browser_error": (
        "页面打开失败，问题在网页可访问性、网络、反爬或浏览器加载层，"
        "而不是抽取器本身。"
    ),
    "no_pages": (
        "工作流没有获取到任何页面；优先检查搜索关键词、seed URL 配置"
        "和浏览器启动。"
    ),
    "no_extracted_jobs": (
        "页面已加载但没有抽取到候选岗位；"
        "通常要检查页面结构、选择器、文本抽取和 visual fallback。"
    ),
    "http_timeout": (
        "HTTP 请求超时，通常是 DNS 解析失败或连接超时；"
        "页面可能已下线或网络受限。"
    ),
    "http_error": (
        "页面返回 HTTP 4xx/5xx 状态码；页面可能已被移除、需要登录，"
        "或反爬机制拒绝访问。"
    ),
    "empty_page": (
        "页面请求成功但正文为空；通常是 JS 渲染页面（需 Playwright "
        "截图）或反爬注入的空壳页面。"
    ),
}

_FALLBACK_FAILURE_EXPLANATION = (
    "未归类失败，需要回到 provider 的 evaluation report 查看 failure_details。"
)


# ── Insight models ─────────────────────────────────────────────────────


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


# ── Insight generation ─────────────────────────────────────────────────


def generate_benchmark_insights(
    result: BenchmarkMatrixResult,
) -> BenchmarkInsight:
    """Generate deterministic Chinese insights from a benchmark matrix."""

    if not result.providers:
        return BenchmarkInsight(
            one_sentence=(
                "还没有可解释的 benchmark 结果；"
                "请先运行 --benchmark-v2 生成 provider matrix。"
            ),
            visual_provider_value="暂无 visual provider 数据。",
            not_prompt_demo_reason=(
                "暂无 benchmark 数据，无法证明它不是一次性 prompt demo。"
            ),
            engineering_judgement=(
                "先补齐 benchmark 结果，再讨论 provider 差异和失败归因。"
            ),
            interview_pitch_60s="我会先运行 benchmark，再用统一指标解释结果。",
            interview_pitch_3min=(
                "目前还没有 provider matrix，"
                "因此下一步是生成 benchmark-v2.json、benchmark-v2.md 和 dashboard，"
                "再做解释层。"
            ),
        )

    best = max(
        result.providers,
        key=lambda p: (p.success_rate, p.total_valid_jobs),
    )
    total_cases = len(result.cases)
    one_sentence = (
        f"这轮 Real Site Benchmark V2 覆盖 {total_cases} 个真实岗位样本，"
        f"当前最好的 provider 是 {best.provider}，"
        f"完成 {best.completed_tasks}/{best.total_tasks}，"
        f"success_rate={best.success_rate:.2f}。"
    )

    provider_notes = [
        _provider_note(p, best.provider) for p in result.providers
    ]
    failure_notes = _failure_notes(result)

    has_visual = any(p.provider == "qwen-vl" for p in result.providers)
    visual_provider_value = (
        "visual provider 的价值在于补足纯文本抽取看不到或结构不稳定的页面信号；"
        "它被放进同一张 provider matrix，而不是单独讲 demo。"
        if has_visual
        else (
            "当前矩阵还没有 visual provider；"
            "下一次可以加入 qwen-vl，用同一批真实页面验证视觉抽取是否带来增益。"
        )
    )
    not_prompt_demo_reason = (
        "它不是 prompt demo，因为样本、workflow、verifier、"
        "失败分类和 provider 输出都被固定成可复跑的评测矩阵。"
    )
    engineering_judgement = (
        "工程判断重点不是宣称某个 provider 永远最好，"
        "而是把成功率、失败类型和页面漂移显式记录下来，让系统能被复盘。"
    )

    interview_pitch_60s = (
        "我把这个项目从能跑通的 Web Agent 做成了可验证的 benchmark。"
        f"同一批真实招聘页面会同时跑 baseline、LLM 和 visual provider，"
        f"然后输出 success rate、valid jobs 和 failure_counts。"
        f"这轮最好的结果是 {best.provider} {best.completed_tasks}/{best.total_tasks}，"
        "失败项也会说明是 verifier 过滤、页面加载还是抽取失败，"
        "所以面试时我能讲清楚边界，而不是只展示一次成功。"
    )
    interview_pitch_3min = (
        "这个 benchmark 的核心设计是把真实网页的不稳定性纳入评测，"
        "而不是假装它不存在。"
        "我先固定真实岗位样本目录，每个 case 都有公司、ATS、岗位族、URL 和 expected signal；"
        "再让不同 provider 走同一个 EvaluationRunner、同一个 verifier、"
        "同一套 failure category。"
        f"输出结果显示当前 best provider 是 {best.provider}，"
        "但我更关心的是为什么它好、哪里失败、失败是否来自页面漂移。"
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
                category, _FALLBACK_FAILURE_EXPLANATION
            ),
        )
        for category, count in sorted(
            totals.items(), key=lambda item: (-item[1], item[0])
        )
    ]


# ── Markdown rendering ─────────────────────────────────────────────────


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
            f"| {note.provider} | {note.completed} "
            f"| {note.success_rate:.2f} | {note.note} |"
        )

    failure_lines = [
        "| Failure Category | Count | 说明 |",
        "|---|---:|---|",
    ]
    if insight.failure_notes:
        for note in insight.failure_notes:
            failure_lines.append(
                f"| {note.failure_category} | {note.count} "
                f"| {note.explanation} |"
            )
    else:
        failure_lines.append("| - | 0 | 本轮没有记录失败分类。 |")

    case_lines = [
        "| Case ID | Company | Role Family | Expected Signal |",
        "|---|---|---|---|",
    ]
    for case in result.cases:
        case_lines.append(
            f"| {case.case_id} | {case.company} "
            f"| {case.role_family} | {case.expected_signal} |"
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
