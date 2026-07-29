"""Tests for the benchmark explanation layer."""

from web_task_agent.benchmark import (
    BenchmarkCase,
    BenchmarkMatrixResult,
    BenchmarkProviderResult,
)
from web_task_agent.benchmark_explainer import (
    BenchmarkInsight,
    generate_benchmark_insights,
    render_benchmark_explanation_markdown,
    write_benchmark_explanation_artifact,
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


# ── Insight generation ─────────────────────────────────────────────────


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
    assert any(
        item.provider == "baseline" for item in insight.provider_notes
    )
    assert any(
        item.failure_category == "verification_filtered"
        for item in insight.failure_notes
    )
    assert insight.interview_pitch_60s.startswith("我把这个项目")


def test_generate_benchmark_insights_handles_empty_matrix():
    insight = generate_benchmark_insights(
        BenchmarkMatrixResult(cases=[], providers=[])
    )

    assert insight.best_provider == ""
    assert "还没有可解释的 benchmark 结果" in insight.one_sentence
    assert insight.provider_notes == []
    assert insight.failure_notes == []


# ── Markdown rendering ─────────────────────────────────────────────────


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
    result = BenchmarkMatrixResult(
        cases=[_case()], providers=[_provider("deepseek", 1, total=1)]
    )
    insight = generate_benchmark_insights(result)

    path = write_benchmark_explanation_artifact(
        result=result,
        insight=insight,
        output_dir=tmp_path,
    )

    assert path.name == "benchmark-v2-explained.md"
    assert "Benchmark V2 结果解释" in path.read_text(encoding="utf-8")


def test_failure_explanations_cover_all_real_categories():
    """Every failure category from the explainer has a Chinese description."""
    from web_task_agent.benchmark_explainer import FAILURE_EXPLANATIONS

    real_categories = {
        "verification_filtered",
        "browser_error",
        "no_pages",
        "no_extracted_jobs",
        "http_timeout",
        "http_error",
        "empty_page",
    }
    assert set(FAILURE_EXPLANATIONS.keys()) == real_categories
