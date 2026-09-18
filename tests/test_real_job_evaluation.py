"""Tests for the versioned real-job evaluation baseline."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from web_task_agent.real_job_evaluation import (
    GroundTruthLabel,
    JobPrediction,
    RealJobCatalogEntry,
    evaluate_run,
    load_catalog,
    load_ground_truth,
    summarize_multi_run,
    write_evaluation_artifacts,
)


def _catalog_entry(case_id: str = "case-1") -> RealJobCatalogEntry:
    return RealJobCatalogEntry(
        case_id=case_id,
        company="Example AI",
        ats="greenhouse",
        role_family="agent-engineering",
        url=f"https://example.com/jobs/{case_id}",
        collected_at=datetime(2026, 9, 9, 8, 36, 43, tzinfo=UTC),
        content_sha256="a" * 64,
        expected_signal="AI agent role",
        source_id="example-official",
    )


def _label(case_id: str = "case-1") -> GroundTruthLabel:
    return GroundTruthLabel(
        case_id=case_id,
        title="AI Agent Intern",
        title_aliases=["AI代理实习生"],
        company="Example AI",
        company_aliases=["Example Artificial Intelligence"],
        location="Shanghai",
        role_family="agent-engineering",
        required_skills=["Python", "RAG"],
        hard_constraints=["2027届"],
        is_relevant=True,
        recommended_top_k=1,
        annotated_by="reviewer",
        annotated_at=datetime(2026, 9, 18, tzinfo=UTC),
    )


def _prediction(case_id: str = "case-1") -> JobPrediction:
    return JobPrediction(
        case_id=case_id,
        title="AI代理实习生",
        company="Example Artificial Intelligence",
        location="上海",
        role_family="agent-engineering",
        skills=["Python", "RAG"],
        match_score=0.9,
        predicted_relevant=True,
        failure_category="",
    )


def test_catalog_requires_https_and_sha256():
    with pytest.raises(ValidationError):
        RealJobCatalogEntry(
            **{
                **_catalog_entry().model_dump(),
                "url": "http://example.com/job",
            }
        )

    with pytest.raises(ValidationError):
        RealJobCatalogEntry(
            **{
                **_catalog_entry().model_dump(),
                "content_sha256": "not-a-hash",
            }
        )


def test_load_catalog_rejects_duplicate_case_ids(tmp_path: Path):
    path = tmp_path / "catalog.json"
    entry = _catalog_entry().model_dump(mode="json")
    path.write_text(
        json.dumps(
            {
                "dataset_version": "real-job-benchmark-v1",
                "collected_at": "2026-09-09T08:36:43Z",
                "cases": [entry, entry],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate case_id"):
        load_catalog(path)


def test_load_ground_truth_rejects_unknown_duplicate_and_unmatched_labels(
    tmp_path: Path,
):
    duplicate_path = tmp_path / "ground-truth-duplicate.json"
    label = _label().model_dump(mode="json")
    duplicate_path.write_text(
        json.dumps(
            {
                "dataset_version": "real-job-benchmark-v1",
                "labels": [label, label],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate case_id"):
        load_ground_truth(duplicate_path)

    catalog = [_catalog_entry()]
    unmatched = load_ground_truth(
        _write_json(
            tmp_path / "ground-truth-unmatched.json",
            {
                "dataset_version": "real-job-benchmark-v1",
                "labels": [
                    _label("unknown-case").model_dump(mode="json"),
                ],
            },
        )
    )
    with pytest.raises(ValueError, match="missing ground truth"):
        evaluate_run(
            dataset_version="real-job-benchmark-v1",
            run_id="run-1",
            provider="baseline",
            cases=catalog,
            labels=unmatched,
            predictions=[_prediction()],
        )


def test_versioned_dataset_has_catalog_ground_truth_and_source_diversity():
    root = Path(__file__).resolve().parents[1]
    dataset_dir = root / "evaluations" / "real-job-benchmark-v1"

    cases = load_catalog(dataset_dir / "catalog.json")
    labels = load_ground_truth(dataset_dir / "ground-truth.json")

    assert len(cases) >= 40
    assert len(labels) == len(cases)
    assert {case.case_id for case in cases} == {label.case_id for label in labels}
    assert len({case.ats for case in cases}) >= 4
    assert len({case.company for case in cases}) >= 8
    assert any(label.is_relevant is True for label in labels)
    assert any(label.recommended_top_k is not None for label in labels)
    assert any(label.required_skills is None for label in labels)

    snapshots_path = dataset_dir / "snapshots.jsonl"
    snapshots = [
        json.loads(line)
        for line in snapshots_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    case_by_id = {case.case_id: case for case in cases}
    assert len(snapshots) == 37
    for snapshot in snapshots:
        content = "\n".join(
            line.strip() for line in snapshot["content"].splitlines() if line.strip()
        )
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert digest == case_by_id[snapshot["case_id"]].content_sha256


def test_evaluate_run_uses_alias_normalization_and_skill_f1():
    summary = evaluate_run(
        dataset_version="real-job-benchmark-v1",
        run_id="run-1",
        provider="baseline",
        cases=[_catalog_entry()],
        labels=[_label()],
        predictions=[_prediction()],
    )

    assert summary.field_metrics.title.accuracy == 1.0
    assert summary.field_metrics.company.accuracy == 1.0
    assert summary.field_metrics.location.accuracy == 0.0
    assert summary.field_metrics.role_family.accuracy == 1.0
    assert summary.skill_metrics.true_positives == 2
    assert summary.skill_metrics.precision == 1.0
    assert summary.skill_metrics.recall == 1.0
    assert summary.skill_metrics.f1 == 1.0
    assert summary.classification.accuracy == 1.0
    assert summary.recommendation.precision_at_k == 1.0
    assert summary.recommendation.recall_at_k == 1.0
    assert summary.recommendation.ndcg_at_k == 1.0


def test_evaluate_run_does_not_fill_missing_ground_truth_with_zero():
    label = _label().model_copy(
        update={
            "location": None,
            "required_skills": None,
            "is_relevant": None,
            "recommended_top_k": None,
        }
    )

    summary = evaluate_run(
        dataset_version="real-job-benchmark-v1",
        run_id="run-2",
        provider="baseline",
        cases=[_catalog_entry()],
        labels=[label],
        predictions=[_prediction()],
    )

    assert summary.field_metrics.location.accuracy is None
    assert summary.skill_metrics.precision is None
    assert summary.classification.accuracy is None
    assert summary.recommendation.precision_at_k is None
    assert summary.recommendation.recall_at_k is None
    assert summary.recommendation.ndcg_at_k is None


def test_evaluate_run_preserves_failure_category_and_counts():
    prediction = JobPrediction(
        case_id="case-1",
        failure_category="source_unavailable",
    )

    summary = evaluate_run(
        dataset_version="real-job-benchmark-v1",
        run_id="run-3",
        provider="baseline",
        cases=[_catalog_entry()],
        labels=[_label()],
        predictions=[prediction],
    )

    assert summary.source_attempts == 1
    assert summary.source_successes == 0
    assert summary.failure_counts == {"source_unavailable": 1}
    assert summary.case_evaluations[0].failure_category == "source_unavailable"


def test_multi_run_summary_reports_mean_min_and_max():
    first = evaluate_run(
        dataset_version="real-job-benchmark-v1",
        run_id="run-1",
        provider="baseline",
        cases=[_catalog_entry()],
        labels=[_label()],
        predictions=[_prediction()],
    )
    second = evaluate_run(
        dataset_version="real-job-benchmark-v1",
        run_id="run-2",
        provider="baseline",
        cases=[_catalog_entry()],
        labels=[_label()],
        predictions=[
            _prediction().model_copy(
                update={
                    "title": "Wrong title",
                    "match_score": 0.2,
                    "predicted_relevant": False,
                }
            )
        ],
    )

    summary = summarize_multi_run([first, second])

    metric = summary.metric_summaries["title_accuracy"]
    assert metric.mean == 0.5
    assert metric.minimum == 0.0
    assert metric.maximum == 1.0


def test_multi_run_summary_keeps_provider_metrics_separate():
    baseline = evaluate_run(
        dataset_version="real-job-benchmark-v1",
        run_id="run-1",
        provider="baseline",
        cases=[_catalog_entry()],
        labels=[_label()],
        predictions=[_prediction().model_copy(update={"title": "Wrong title"})],
    )
    snapshot = evaluate_run(
        dataset_version="real-job-benchmark-v1",
        run_id="run-2",
        provider="snapshot-baseline",
        cases=[_catalog_entry()],
        labels=[_label()],
        predictions=[_prediction()],
    )

    summary = summarize_multi_run([baseline, snapshot])

    assert summary.provider_summaries["baseline"].metric_summaries["title_accuracy"].mean == 0.0
    assert (
        summary.provider_summaries["snapshot-baseline"].metric_summaries["title_accuracy"].mean
        == 1.0
    )


def test_write_evaluation_artifacts_contains_no_secret_markers(tmp_path: Path):
    summary = evaluate_run(
        dataset_version="real-job-benchmark-v1",
        run_id="run-1",
        provider="baseline",
        cases=[_catalog_entry()],
        labels=[_label()],
        predictions=[_prediction()],
    )

    paths = write_evaluation_artifacts(summary, tmp_path)
    content = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert {path.name for path in paths} == {
        "summary.json",
        "summary.md",
        "summary.html",
    }
    assert "DASHSCOPE_API_KEY" not in content
    assert "Authorization" not in content
    assert "resume_text" not in content


def test_multi_run_markdown_has_provider_breakdown(tmp_path: Path):
    run = evaluate_run(
        dataset_version="real-job-benchmark-v1",
        run_id="run-1",
        provider="snapshot-baseline",
        cases=[_catalog_entry()],
        labels=[_label()],
        predictions=[_prediction()],
    )
    summary = summarize_multi_run([run])

    markdown_path = write_evaluation_artifacts(summary, tmp_path)[1]
    markdown = markdown_path.read_text(encoding="utf-8")

    assert "## Provider Summary" in markdown
    assert "| snapshot-baseline |" in markdown


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path
