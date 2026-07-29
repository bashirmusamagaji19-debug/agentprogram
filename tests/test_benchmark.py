"""Tests for real-site benchmark v2 catalog, matrix, and rendering."""

import pytest

from web_task_agent.benchmark import (
    BenchmarkCase,
    BenchmarkMatrixResult,
    BenchmarkProviderResult,
    build_real_site_benchmark_v2_cases,
    parse_benchmark_providers,
    render_benchmark_markdown,
    run_benchmark_matrix,
)
from web_task_agent.evaluation import EvaluationResult, TaskEvaluationResult


# ── Task 1: catalog & provider parsing ─────────────────────────────────


def test_real_site_benchmark_v2_catalog_has_metadata_and_tasks():
    cases = build_real_site_benchmark_v2_cases()

    assert len(cases) >= 8
    assert {case.ats for case in cases} >= {"greenhouse"}
    assert {case.company for case in cases} >= {
        "Anthropic",
        "ScaleAI",
        "Reddit",
        "Discord",
    }
    assert all(case.case_id for case in cases)
    assert all(case.url.startswith("https://") for case in cases)

    task = cases[0].to_evaluation_task()
    assert task.seed_urls == [cases[0].url]
    assert task.keyword == cases[0].keyword
    assert task.location == cases[0].location
    assert task.skills == cases[0].skills


def test_benchmark_case_rejects_empty_url():
    with pytest.raises(ValueError, match="url"):
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


def test_parse_benchmark_providers_defaults_and_dedupes():
    assert parse_benchmark_providers("") == ["baseline", "llm-demo"]
    assert parse_benchmark_providers(None) == ["baseline", "llm-demo"]
    assert parse_benchmark_providers(
        "baseline,llm-demo,baseline,deepseek"
    ) == ["baseline", "llm-demo", "deepseek"]


def test_parse_benchmark_providers_rejects_unknown():
    with pytest.raises(ValueError, match="Unsupported benchmark provider"):
        parse_benchmark_providers("baseline,unknown-provider")


# ── Task 2: matrix results & Markdown rendering ────────────────────────


def _fake_eval(
    *, completed: int, total: int, failures: dict[str, int] | None = None
) -> EvaluationResult:
    return EvaluationResult(
        total_tasks=total,
        completed_tasks=completed,
        success_rate=round(completed / total, 2) if total else 0.0,
        total_valid_jobs=completed,
        average_pages_visited=1.0,
        failure_counts=failures or {},
        task_results=[
            TaskEvaluationResult(
                keyword="AI Builder Intern",
                location="Remote",
                pages_visited=1,
                valid_jobs=1 if completed else 0,
                success=bool(completed),
                failure_category="" if completed else "verification_filtered",
                failure_reason="" if completed else "no valid jobs",
                failure_details=""
                if completed
                else "confidence below 0.5",
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
                result=_fake_eval(
                    completed=0, total=1, failures={"verification_filtered": 1}
                ),
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


def test_benchmark_matrix_best_provider():
    result = BenchmarkMatrixResult(
        cases=build_real_site_benchmark_v2_cases()[:1],
        providers=[
            BenchmarkProviderResult.from_evaluation(
                provider="baseline",
                result=_fake_eval(completed=0, total=1, failures={}),
                elapsed_seconds=0.1,
            ),
            BenchmarkProviderResult.from_evaluation(
                provider="deepseek",
                result=_fake_eval(completed=1, total=1, failures={}),
                elapsed_seconds=0.2,
            ),
        ],
    )
    assert result.best_provider == "deepseek"


# ── Task 3: matrix runner ─────────────────────────────────────────────


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
    assert [p.provider for p in result.providers] == [
        "baseline",
        "llm-demo",
        "deepseek",
    ]
    assert all(p.total_tasks == 2 for p in result.providers)
