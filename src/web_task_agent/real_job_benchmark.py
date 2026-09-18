"""Repeatable provider runs over the versioned real-job benchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from web_task_agent.evaluation import EvaluationRunner, EvaluationTask, TaskEvaluationResult
from web_task_agent.extractor import PageExtractor
from web_task_agent.llm_extractor import DemoLlmFieldExtractor
from web_task_agent.models import BrowserPage
from web_task_agent.real_job_evaluation import (
    GroundTruthLabel,
    JobPrediction,
    MultiRunSummary,
    RealJobCatalogEntry,
    evaluate_run,
    load_catalog,
    load_dataset_version,
    load_ground_truth,
    summarize_multi_run,
    write_evaluation_artifacts,
)

SUPPORTED_PROVIDERS = {
    "baseline",
    "llm-demo",
    "snapshot-baseline",
    "snapshot-llm-demo",
}
REQUIRED_KEYWORDS = ["AI", "大模型", "算法", "模型", "智能", "机器人"]
PROFILE_SKILLS = ["Python", "LLM", "RAG", "LangGraph", "Agent"]
PROFILE_RESUME = "3 年 Python 后端，熟悉 LangGraph、RAG、FastAPI、LLM Agent。"

BenchmarkRunner = Callable[
    [str, list[RealJobCatalogEntry], Path],
    Awaitable[list[JobPrediction]],
]


def parse_providers(raw: str) -> list[str]:
    providers: list[str] = []
    for item in raw.split(","):
        provider = item.strip()
        if not provider:
            continue
        if provider not in SUPPORTED_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
            raise ValueError(
                f"Unsupported real-job benchmark provider: {provider!r}. Supported: {supported}"
            )
        if provider not in providers:
            providers.append(provider)
    return providers or ["baseline"]


async def run_real_job_benchmark(
    *,
    dataset_version: str,
    catalog: list[RealJobCatalogEntry],
    labels: list[GroundTruthLabel],
    providers: list[str],
    runs: int,
    output_dir: str | Path,
    runner: BenchmarkRunner | None = None,
    offline_predictions: str | Path | None = None,
    offline_snapshots: str | Path | None = None,
    recommendation_k: int = 10,
) -> MultiRunSummary:
    if runs < 1:
        raise ValueError("runs must be at least 1")
    if not providers:
        raise ValueError("at least one provider is required")
    if runner is not None and offline_predictions is not None:
        raise ValueError("runner and offline_predictions are mutually exclusive")

    output = Path(output_dir)
    offline_by_provider = _load_offline_predictions(offline_predictions)
    snapshot_pages = _load_snapshot_pages(offline_snapshots)
    run_summaries = []
    for run_index in range(1, runs + 1):
        for provider in providers:
            run_id = _run_id(run_index, provider)
            run_dir = output / "runs" / run_id
            started_at = datetime.now(UTC)
            started_clock = perf_counter()
            if runner is not None:
                predictions = await runner(provider, catalog, run_dir)
            elif offline_by_provider is not None:
                try:
                    predictions = [
                        JobPrediction.model_validate(item) for item in offline_by_provider[provider]
                    ]
                except KeyError as exc:
                    raise ValueError(f"offline predictions missing provider: {provider}") from exc
            elif provider.startswith("snapshot-"):
                if snapshot_pages is None:
                    raise ValueError("snapshot providers require offline_snapshots")
                predictions = await _run_snapshot_provider(
                    provider=provider,
                    cases=catalog,
                    pages=snapshot_pages,
                    output_dir=run_dir,
                )
            else:
                predictions = await _run_live_provider(
                    provider=provider,
                    cases=catalog,
                    output_dir=run_dir,
                )
            finished_at = datetime.now(UTC)
            summary = evaluate_run(
                dataset_version=dataset_version,
                run_id=run_id,
                provider=provider,
                cases=catalog,
                labels=labels,
                predictions=predictions,
                recommendation_k=recommendation_k,
                started_at=started_at,
                finished_at=finished_at,
            )
            summary = summary.model_copy(
                update={"elapsed_seconds": round(perf_counter() - started_clock, 4)}
            )
            write_evaluation_artifacts(summary, run_dir)
            run_summaries.append(summary)

    multi_run = summarize_multi_run(run_summaries)
    write_evaluation_artifacts(multi_run, output)
    return multi_run


async def _run_live_provider(
    *,
    provider: str,
    cases: list[RealJobCatalogEntry],
    output_dir: Path,
) -> list[JobPrediction]:
    from web_task_agent.browser import BrowserUseClient, HttpPageLoader

    tasks = [_to_evaluation_task(case) for case in cases]

    def llm_extractor_factory(task):
        return PageExtractor(llm_field_extractor=DemoLlmFieldExtractor())

    if provider == "llm-demo":
        extractor_factory = llm_extractor_factory
    elif provider == "baseline":
        extractor_factory = None
    else:
        raise ValueError(f"Unsupported real-job benchmark provider: {provider!r}")

    result = await EvaluationRunner(
        output_dir=output_dir,
        browser_factory=lambda task: BrowserUseClient(page_loader=HttpPageLoader()),
        extractor_factory=extractor_factory,
    ).run(tasks=tasks)
    return [_prediction_from_task(task_result) for task_result in result.task_results]


async def _run_snapshot_provider(
    *,
    provider: str,
    cases: list[RealJobCatalogEntry],
    pages: dict[str, BrowserPage],
    output_dir: Path,
) -> list[JobPrediction]:
    from web_task_agent.browser import FakeBrowserClient

    available_cases = [case for case in cases if case.case_id in pages]
    tasks = [_to_evaluation_task(case) for case in available_cases]
    extractor_factory = None

    def llm_extractor_factory(task):
        return PageExtractor(llm_field_extractor=DemoLlmFieldExtractor())

    if provider == "snapshot-baseline":
        pass
    elif provider == "snapshot-llm-demo":
        extractor_factory = llm_extractor_factory
    else:
        raise ValueError(f"Unsupported snapshot provider: {provider!r}")

    result = await EvaluationRunner(
        output_dir=output_dir,
        browser_factory=lambda task: FakeBrowserClient([pages[task.case_id]]),
        extractor_factory=extractor_factory,
    ).run(tasks=tasks)
    predictions_by_case = {
        prediction.case_id: prediction
        for prediction in (
            _prediction_from_task(task_result) for task_result in result.task_results
        )
    }
    for case in cases:
        if case.case_id not in predictions_by_case:
            predictions_by_case[case.case_id] = JobPrediction(
                case_id=case.case_id,
                failure_category="source_unavailable",
            )
    return [predictions_by_case[case.case_id] for case in cases]


def _to_evaluation_task(case: RealJobCatalogEntry) -> EvaluationTask:
    return EvaluationTask(
        case_id=case.case_id,
        keyword=case.expected_signal,
        location="全国",
        target_count=1,
        skills=PROFILE_SKILLS,
        resume_text=PROFILE_RESUME,
        seed_urls=[case.url],
        required_keywords=REQUIRED_KEYWORDS,
    )


def _prediction_from_task(task_result: TaskEvaluationResult) -> JobPrediction:
    if not task_result.jobs:
        details = task_result.failure_details.casefold()
        failure_category = task_result.failure_category or "extraction_missing"
        if "http 404" in details or "not found" in details:
            failure_category = "page_drift"
        elif task_result.failure_category == "browser_error":
            failure_category = "source_unavailable"
        return JobPrediction(
            case_id=task_result.case_id,
            failure_category=failure_category,
            pages_visited=task_result.pages_visited,
            valid_jobs=task_result.valid_jobs,
        )
    job = task_result.jobs[0]
    match = task_result.matches[0] if task_result.matches else None
    match_score = match.score if match else None
    return JobPrediction.from_job(
        case_id=task_result.case_id,
        job=job,
        match_score=match_score,
        predicted_relevant=(match_score >= 0.5 if match_score is not None else None),
        pages_visited=task_result.pages_visited,
    ).model_copy(
        update={
            "role_family": _role_family_from_category(job.category),
            "valid_jobs": task_result.valid_jobs,
        }
    )


def _role_family_from_category(category: str) -> str:
    return {
        "算法": "algorithm",
        "工程": "engineering",
        "产品": "product-business",
        "运营": "operations",
        "设计": "design",
    }.get(category, "")


def _load_offline_predictions(
    path: str | Path | None,
) -> dict[str, list[dict]] | None:
    if path is None:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("offline predictions root must be an object")
    return payload


def _load_snapshot_pages(
    path: str | Path | None,
) -> dict[str, BrowserPage] | None:
    if path is None:
        return None
    pages: dict[str, BrowserPage] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        page = BrowserPage(
            url=item["url"],
            title=item.get("title", ""),
            content=item["content"],
            source="frozen-snapshot",
            metadata={"case_id": item["case_id"]},
        )
        pages[item["case_id"]] = page
    return pages


def _run_id(run_index: int, provider: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{timestamp}-{run_index:02d}-{provider}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a versioned real-job benchmark over fixed URLs."
    )
    parser.add_argument("--dataset-version", default="real-job-benchmark-v1")
    parser.add_argument(
        "--catalog",
        default="evaluations/real-job-benchmark-v1/catalog.json",
    )
    parser.add_argument(
        "--ground-truth",
        default="evaluations/real-job-benchmark-v1/ground-truth.json",
    )
    parser.add_argument("--providers", default="baseline")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument(
        "--output-dir",
        default="evaluations/real-job-benchmark-v1",
    )
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--predictions")
    parser.add_argument(
        "--snapshots",
        default="evaluations/real-job-benchmark-v1/snapshots.jsonl",
    )
    parser.add_argument("--recommendation-k", type=int, default=10)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    catalog_version = load_dataset_version(args.catalog)
    if catalog_version != args.dataset_version:
        raise SystemExit(
            f"dataset version mismatch: catalog={catalog_version!r}, "
            f"requested={args.dataset_version!r}"
        )
    catalog = load_catalog(args.catalog)
    labels = load_ground_truth(args.ground_truth)
    providers = parse_providers(args.providers)
    summary = asyncio.run(
        run_real_job_benchmark(
            dataset_version=args.dataset_version,
            catalog=catalog,
            labels=labels,
            providers=providers,
            runs=args.runs,
            output_dir=args.output_dir,
            offline_predictions=args.predictions if args.offline else None,
            offline_snapshots=args.snapshots if args.offline else None,
            recommendation_k=args.recommendation_k,
        )
    )
    print(f"Real-job benchmark runs: {summary.run_count}")
    print(f"Summary directory: {Path(args.output_dir).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
