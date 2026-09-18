"""Ground-truth evaluation for versioned real-job benchmark runs."""

from __future__ import annotations

import html
import json
import math
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from pydantic import BaseModel, Field, field_validator

from web_task_agent.models import JobPosting
from web_task_agent.skill_aliases import normalize_skill

DATASET_VERSION = "real-job-benchmark-v1"


def _require_non_empty(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("value must not be empty")
    return value


class RealJobCatalogEntry(BaseModel):
    """Immutable metadata for one sampled real job URL."""

    case_id: str
    company: str
    ats: str
    role_family: str
    url: str
    collected_at: datetime
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_signal: str
    source_id: str

    @field_validator("case_id", "company", "ats", "role_family", "source_id")
    @classmethod
    def _clean_required_text(cls, value: str) -> str:
        return _require_non_empty(value)

    @field_validator("url")
    @classmethod
    def _require_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("url must be an https URL")
        return value

    @field_validator("collected_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("collected_at must be timezone-aware")
        return value.astimezone(UTC)


class GroundTruthLabel(BaseModel):
    """Human-reviewed labels for one catalog case."""

    case_id: str
    title: str | None = None
    title_aliases: list[str] = Field(default_factory=list)
    company: str | None = None
    company_aliases: list[str] = Field(default_factory=list)
    location: str | None = None
    location_aliases: list[str] = Field(default_factory=list)
    role_family: str | None = None
    required_skills: list[str] | None = None
    hard_constraints: list[str] | None = None
    is_relevant: bool | None = None
    recommended_top_k: int | None = Field(default=None, ge=1)
    annotated_by: str
    annotated_at: datetime

    @field_validator("case_id", "annotated_by")
    @classmethod
    def _clean_required_text(cls, value: str) -> str:
        return _require_non_empty(value)

    @field_validator("annotated_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("annotated_at must be timezone-aware")
        return value.astimezone(UTC)


class JobPrediction(BaseModel):
    """Provider prediction used for scoring; never persisted with raw pages."""

    case_id: str
    title: str = ""
    company: str = ""
    location: str = ""
    role_family: str = ""
    skills: list[str] = Field(default_factory=list)
    match_score: float | None = None
    predicted_relevant: bool | None = None
    failure_category: str = ""
    pages_visited: int = Field(default=0, ge=0)
    valid_jobs: int = Field(default=0, ge=0)

    @field_validator("case_id")
    @classmethod
    def _clean_case_id(cls, value: str) -> str:
        return _require_non_empty(value)

    @classmethod
    def from_job(
        cls,
        *,
        case_id: str,
        job: JobPosting,
        match_score: float | None,
        predicted_relevant: bool | None,
        failure_category: str = "",
        pages_visited: int = 1,
    ) -> JobPrediction:
        return cls(
            case_id=case_id,
            title=job.title,
            company=job.company,
            location=job.location,
            skills=job.skills,
            match_score=match_score,
            predicted_relevant=predicted_relevant,
            failure_category=failure_category,
            pages_visited=pages_visited,
            valid_jobs=1,
        )


class AccuracyMetric(BaseModel):
    correct: int = Field(ge=0)
    labeled: int = Field(ge=0)
    accuracy: float | None = None


class FieldMetrics(BaseModel):
    title: AccuracyMetric
    company: AccuracyMetric
    location: AccuracyMetric
    role_family: AccuracyMetric


class SkillMetrics(BaseModel):
    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)
    labeled: int = Field(ge=0)
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None


class ClassificationMetrics(BaseModel):
    correct: int = Field(ge=0)
    labeled: int = Field(ge=0)
    accuracy: float | None = None


class RecommendationMetrics(BaseModel):
    k: int = Field(ge=0)
    predicted_relevant: int = Field(ge=0)
    ground_truth_relevant: int = Field(ge=0)
    hits: int = Field(default=0, ge=0)
    precision_at_k: float | None = None
    recall_at_k: float | None = None
    ndcg_at_k: float | None = None


class CaseEvaluation(BaseModel):
    case_id: str
    title_correct: bool | None
    company_correct: bool | None
    location_correct: bool | None
    role_family_correct: bool | None
    skill_true_positives: int = Field(ge=0)
    skill_false_positives: int = Field(ge=0)
    skill_false_negatives: int = Field(ge=0)
    relevance_correct: bool | None
    failure_category: str = ""


class RunEvaluationSummary(BaseModel):
    dataset_version: str
    run_id: str
    provider: str
    started_at: datetime
    finished_at: datetime
    elapsed_seconds: float = Field(ge=0)
    cases_total: int = Field(ge=0)
    source_attempts: int = Field(ge=0)
    source_successes: int = Field(ge=0)
    pages_visited: int = Field(ge=0)
    valid_jobs: int = Field(ge=0)
    failure_counts: dict[str, int] = Field(default_factory=dict)
    field_metrics: FieldMetrics
    skill_metrics: SkillMetrics
    classification: ClassificationMetrics
    recommendation: RecommendationMetrics
    case_evaluations: list[CaseEvaluation]


class MetricSummary(BaseModel):
    mean: float
    minimum: float
    maximum: float


class ProviderRunSummary(BaseModel):
    provider: str
    run_count: int = Field(ge=0)
    metric_summaries: dict[str, MetricSummary]


class MultiRunSummary(BaseModel):
    dataset_version: str
    run_count: int = Field(ge=0)
    providers: list[str]
    metric_summaries: dict[str, MetricSummary]
    provider_summaries: dict[str, ProviderRunSummary]
    runs: list[RunEvaluationSummary]


def load_catalog(path: str | Path) -> list[RealJobCatalogEntry]:
    payload = _load_json_object(path)
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("catalog must contain a cases array")
    cases = [RealJobCatalogEntry.model_validate(item) for item in raw_cases]
    _require_unique_case_ids(cases, "catalog")
    return cases


def load_dataset_version(path: str | Path) -> str:
    payload = _load_json_object(path)
    version = payload.get("dataset_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("dataset_version must be a non-empty string")
    return version.strip()


def load_ground_truth(path: str | Path) -> list[GroundTruthLabel]:
    payload = _load_json_object(path)
    raw_labels = payload.get("labels")
    if not isinstance(raw_labels, list):
        raise ValueError("ground truth must contain a labels array")
    labels = [GroundTruthLabel.model_validate(item) for item in raw_labels]
    _require_unique_case_ids(labels, "ground truth")
    return labels


def evaluate_run(
    *,
    dataset_version: str,
    run_id: str,
    provider: str,
    cases: list[RealJobCatalogEntry],
    labels: list[GroundTruthLabel],
    predictions: list[JobPrediction],
    recommendation_k: int = 10,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> RunEvaluationSummary:
    case_ids = [case.case_id for case in cases]
    label_by_case = {label.case_id: label for label in labels}
    prediction_by_case = {prediction.case_id: prediction for prediction in predictions}
    missing_labels = [case_id for case_id in case_ids if case_id not in label_by_case]
    unknown_labels = [case_id for case_id in label_by_case if case_id not in case_ids]
    unknown_predictions = [case_id for case_id in prediction_by_case if case_id not in case_ids]
    if missing_labels:
        raise ValueError(f"missing ground truth for case_ids: {missing_labels}")
    if unknown_labels:
        raise ValueError(f"ground truth has unknown case_ids: {unknown_labels}")
    if unknown_predictions:
        raise ValueError(f"predictions have unknown case_ids: {unknown_predictions}")

    case_evaluations: list[CaseEvaluation] = []
    field_results: dict[str, list[bool | None]] = {
        "title": [],
        "company": [],
        "location": [],
        "role_family": [],
    }
    skill_true_positives = 0
    skill_false_positives = 0
    skill_false_negatives = 0
    skill_labels_seen = 0
    relevance_correct = 0
    relevance_labeled = 0
    failure_counts: dict[str, int] = {}
    source_successes = 0

    for case in cases:
        label = label_by_case[case.case_id]
        prediction = prediction_by_case.get(
            case.case_id,
            JobPrediction(
                case_id=case.case_id,
                failure_category="extraction_missing",
            ),
        )
        title_correct = _field_match(
            prediction.title,
            label.title,
            label.title_aliases,
        )
        company_correct = _field_match(
            prediction.company,
            label.company,
            label.company_aliases,
        )
        location_correct = _field_match(
            prediction.location,
            label.location,
            label.location_aliases,
        )
        role_family_correct = _field_match(
            prediction.role_family,
            label.role_family,
            [],
        )
        field_results["title"].append(title_correct)
        field_results["company"].append(company_correct)
        field_results["location"].append(location_correct)
        field_results["role_family"].append(role_family_correct)

        true_positives, false_positives, false_negatives = _skill_counts(
            prediction.skills,
            label.required_skills,
        )
        if label.required_skills is not None:
            skill_labels_seen += 1
        skill_true_positives += true_positives
        skill_false_positives += false_positives
        skill_false_negatives += false_negatives

        relevance_result: bool | None = None
        if label.is_relevant is not None:
            relevance_labeled += 1
            relevance_result = (
                prediction.predicted_relevant is not None
                and prediction.predicted_relevant == label.is_relevant
            )
            if relevance_result:
                relevance_correct += 1

        if prediction.failure_category:
            failure_counts[prediction.failure_category] = (
                failure_counts.get(prediction.failure_category, 0) + 1
            )
        elif prediction.valid_jobs > 0:
            source_successes += 1

        case_evaluations.append(
            CaseEvaluation(
                case_id=case.case_id,
                title_correct=title_correct,
                company_correct=company_correct,
                location_correct=location_correct,
                role_family_correct=role_family_correct,
                skill_true_positives=true_positives,
                skill_false_positives=false_positives,
                skill_false_negatives=false_negatives,
                relevance_correct=relevance_result,
                failure_category=prediction.failure_category,
            )
        )

    field_metrics = FieldMetrics(
        title=_accuracy(field_results["title"]),
        company=_accuracy(field_results["company"]),
        location=_accuracy(field_results["location"]),
        role_family=_accuracy(field_results["role_family"]),
    )
    skill_metrics = _skill_metrics(
        true_positives=skill_true_positives,
        false_positives=skill_false_positives,
        false_negatives=skill_false_negatives,
        labeled=skill_labels_seen,
    )
    classification = ClassificationMetrics(
        correct=relevance_correct,
        labeled=relevance_labeled,
        accuracy=(round(relevance_correct / relevance_labeled, 4) if relevance_labeled else None),
    )
    recommendation = _recommendation_metrics(
        labels=labels,
        predictions=[prediction_by_case.get(case_id) for case_id in case_ids],
        k=recommendation_k,
    )
    start = started_at or datetime.now(UTC)
    finish = finished_at or start
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("started_at must be timezone-aware")
    if finish.tzinfo is None or finish.utcoffset() is None:
        raise ValueError("finished_at must be timezone-aware")
    start = start.astimezone(UTC)
    finish = finish.astimezone(UTC)

    return RunEvaluationSummary(
        dataset_version=dataset_version,
        run_id=run_id,
        provider=provider,
        started_at=start,
        finished_at=finish,
        elapsed_seconds=max((finish - start).total_seconds(), 0.0),
        cases_total=len(cases),
        source_attempts=len(cases),
        source_successes=source_successes,
        pages_visited=sum(prediction.pages_visited for prediction in prediction_by_case.values()),
        valid_jobs=sum(prediction.valid_jobs for prediction in prediction_by_case.values()),
        failure_counts=failure_counts,
        field_metrics=field_metrics,
        skill_metrics=skill_metrics,
        classification=classification,
        recommendation=recommendation,
        case_evaluations=case_evaluations,
    )


def summarize_multi_run(runs: list[RunEvaluationSummary]) -> MultiRunSummary:
    summaries = _summarize_metric_values(runs)
    providers = sorted({run.provider for run in runs})
    provider_summaries = {
        provider: ProviderRunSummary(
            provider=provider,
            run_count=sum(1 for run in runs if run.provider == provider),
            metric_summaries=_summarize_metric_values(
                [run for run in runs if run.provider == provider]
            ),
        )
        for provider in providers
    }
    dataset_version = runs[0].dataset_version if runs else DATASET_VERSION
    return MultiRunSummary(
        dataset_version=dataset_version,
        run_count=len(runs),
        providers=providers,
        metric_summaries=summaries,
        provider_summaries=provider_summaries,
        runs=runs,
    )


def _summarize_metric_values(
    runs: list[RunEvaluationSummary],
) -> dict[str, MetricSummary]:
    metric_values: dict[str, list[float]] = {}
    for run in runs:
        values = {
            "title_accuracy": run.field_metrics.title.accuracy,
            "company_accuracy": run.field_metrics.company.accuracy,
            "location_accuracy": run.field_metrics.location.accuracy,
            "role_family_accuracy": run.field_metrics.role_family.accuracy,
            "skill_precision": run.skill_metrics.precision,
            "skill_recall": run.skill_metrics.recall,
            "skill_f1": run.skill_metrics.f1,
            "classification_accuracy": run.classification.accuracy,
            "precision_at_k": run.recommendation.precision_at_k,
            "recall_at_k": run.recommendation.recall_at_k,
            "ndcg_at_k": run.recommendation.ndcg_at_k,
        }
        for name, value in values.items():
            if value is not None:
                metric_values.setdefault(name, []).append(value)

    return {
        name: MetricSummary(
            mean=round(mean(values), 4),
            minimum=round(min(values), 4),
            maximum=round(max(values), 4),
        )
        for name, values in metric_values.items()
    }


def write_evaluation_artifacts(
    summary: RunEvaluationSummary | MultiRunSummary,
    output_dir: str | Path,
) -> tuple[Path, Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "summary.json"
    markdown_path = output / "summary.md"
    html_path = output / "summary.html"
    json_path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    markdown_path.write_text(_render_markdown(summary), encoding="utf-8")
    html_path.write_text(_render_html(summary), encoding="utf-8")
    return json_path, markdown_path, html_path


def _load_json_object(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    return payload


def _require_unique_case_ids(items: list[Any], label: str) -> None:
    seen: set[str] = set()
    for item in items:
        if item.case_id in seen:
            raise ValueError(f"duplicate case_id in {label}: {item.case_id}")
        seen.add(item.case_id)


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    characters = [character if character.isalnum() else " " for character in normalized]
    return " ".join("".join(characters).split())


def _field_match(value: str, truth: str | None, aliases: list[str]) -> bool | None:
    if truth is None:
        return None
    normalized_value = _normalize_text(value)
    if not normalized_value:
        return False
    candidates = [truth, *aliases]
    return any(_normalize_text(candidate) == normalized_value for candidate in candidates)


def _skill_counts(
    predicted_skills: list[str],
    required_skills: list[str] | None,
) -> tuple[int, int, int]:
    if required_skills is None:
        return 0, 0, 0
    predicted = {normalize_skill(skill) for skill in predicted_skills if skill.strip()}
    required = {normalize_skill(skill) for skill in required_skills if skill.strip()}
    true_positives = len(predicted & required)
    return (
        true_positives,
        len(predicted - required),
        len(required - predicted),
    )


def _accuracy(results: list[bool | None]) -> AccuracyMetric:
    labeled_results = [result for result in results if result is not None]
    correct = sum(1 for result in labeled_results if result)
    return AccuracyMetric(
        correct=correct,
        labeled=len(labeled_results),
        accuracy=(round(correct / len(labeled_results), 4) if labeled_results else None),
    )


def _skill_metrics(
    *,
    true_positives: int,
    false_positives: int,
    false_negatives: int,
    labeled: int,
) -> SkillMetrics:
    if labeled == 0:
        return SkillMetrics(
            true_positives=0,
            false_positives=0,
            false_negatives=0,
            labeled=0,
        )
    predicted_positive = true_positives + false_positives
    ground_truth_positive = true_positives + false_negatives
    precision = true_positives / predicted_positive if predicted_positive else 0.0
    recall = true_positives / ground_truth_positive if ground_truth_positive else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return SkillMetrics(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        labeled=labeled,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
    )


def _recommendation_metrics(
    *,
    labels: list[GroundTruthLabel],
    predictions: list[JobPrediction | None],
    k: int,
) -> RecommendationMetrics:
    if k < 1:
        raise ValueError("recommendation k must be at least 1")
    relevant_labels = {label.case_id for label in labels if label.is_relevant is True}
    if not relevant_labels:
        return RecommendationMetrics(
            k=k,
            predicted_relevant=0,
            ground_truth_relevant=0,
        )
    ranked = sorted(
        (
            prediction
            for prediction in predictions
            if prediction is not None and prediction.predicted_relevant is True
        ),
        key=lambda prediction: (
            prediction.match_score if prediction.match_score is not None else float("-inf")
        ),
        reverse=True,
    )
    effective_k = min(k, len(predictions))
    top_k = ranked[:effective_k]
    gains = [1.0 if prediction.case_id in relevant_labels else 0.0 for prediction in top_k]
    hits = int(sum(gains))
    dcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))
    ideal_gains = [1.0] * min(effective_k, len(relevant_labels))
    idcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(ideal_gains, start=1))
    return RecommendationMetrics(
        k=k,
        predicted_relevant=len(ranked),
        ground_truth_relevant=len(relevant_labels),
        hits=hits,
        precision_at_k=round(hits / effective_k, 4) if effective_k else 0.0,
        recall_at_k=round(hits / len(relevant_labels), 4),
        ndcg_at_k=round(dcg / idcg, 4) if idcg else 0.0,
    )


def _render_markdown(summary: RunEvaluationSummary | MultiRunSummary) -> str:
    if isinstance(summary, MultiRunSummary):
        lines = [
            "# Real Job Benchmark",
            "",
            f"- Dataset: `{summary.dataset_version}`",
            f"- Runs: {summary.run_count}",
            f"- Providers: {', '.join(summary.providers) or '-'}",
            "",
            "## Metric Summary",
            "",
            "| Metric | Mean | Min | Max |",
            "|---|---:|---:|---:|",
        ]
        for name, metric in sorted(summary.metric_summaries.items()):
            lines.append(
                f"| {name} | {metric.mean:.4f} | {metric.minimum:.4f} | {metric.maximum:.4f} |"
            )
        lines.extend(
            [
                "",
                "## Provider Summary",
                "",
                "| Provider | Runs | Title | Company | Relevance | P@K | R@K | NDCG@K |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for provider in summary.providers:
            provider_summary = summary.provider_summaries[provider]
            metrics = provider_summary.metric_summaries
            lines.append(
                f"| {provider} | {provider_summary.run_count} "
                f"| {_metric_mean(metrics, 'title_accuracy')} "
                f"| {_metric_mean(metrics, 'company_accuracy')} "
                f"| {_metric_mean(metrics, 'classification_accuracy')} "
                f"| {_metric_mean(metrics, 'precision_at_k')} "
                f"| {_metric_mean(metrics, 'recall_at_k')} "
                f"| {_metric_mean(metrics, 'ndcg_at_k')} |"
            )
        lines.append("")
        return "\n".join(lines)

    lines = [
        "# Real Job Benchmark Run",
        "",
        f"- Dataset: `{summary.dataset_version}`",
        f"- Run ID: `{summary.run_id}`",
        f"- Provider: `{summary.provider}`",
        f"- Source attempts: {summary.source_attempts}",
        f"- Source successes: {summary.source_successes}",
        f"- Valid jobs: {summary.valid_jobs}",
        "",
        "## Metrics",
        "",
        f"- Title accuracy: {_format_metric(summary.field_metrics.title.accuracy)}",
        f"- Company accuracy: {_format_metric(summary.field_metrics.company.accuracy)}",
        f"- Location accuracy: {_format_metric(summary.field_metrics.location.accuracy)}",
        f"- Role-family accuracy: {_format_metric(summary.field_metrics.role_family.accuracy)}",
        f"- Skill precision: {_format_metric(summary.skill_metrics.precision)}",
        f"- Skill recall: {_format_metric(summary.skill_metrics.recall)}",
        f"- Skill F1: {_format_metric(summary.skill_metrics.f1)}",
        f"- Relevance accuracy: {_format_metric(summary.classification.accuracy)}",
        f"- Precision@K: {_format_metric(summary.recommendation.precision_at_k)}",
        f"- Recall@K: {_format_metric(summary.recommendation.recall_at_k)}",
        f"- NDCG@K: {_format_metric(summary.recommendation.ndcg_at_k)}",
        "",
    ]
    return "\n".join(lines)


def _format_metric(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.4f}"


def _metric_mean(metrics: dict[str, MetricSummary], name: str) -> str:
    metric = metrics.get(name)
    return "-" if metric is None else f"{metric.mean:.4f}"


def _render_html(summary: RunEvaluationSummary | MultiRunSummary) -> str:
    markdown = _render_markdown(summary)
    escaped = html.escape(markdown)
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        "<title>Real Job Benchmark</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:960px;margin:2rem auto;"
        "padding:0 1rem;color:#17211b}pre{white-space:pre-wrap;line-height:1.6}</style>"
        f"</head><body><pre>{escaped}</pre></body></html>"
    )
