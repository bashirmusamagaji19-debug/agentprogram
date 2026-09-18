"""Tests for the repeatable real-job benchmark runner."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from web_task_agent.real_job_benchmark import (
    parse_providers,
    run_real_job_benchmark,
)
from web_task_agent.real_job_evaluation import (
    GroundTruthLabel,
    JobPrediction,
    RealJobCatalogEntry,
)


def _case(index: int) -> RealJobCatalogEntry:
    return RealJobCatalogEntry(
        case_id=f"case-{index}",
        company=f"Company {index}",
        ats="fixture",
        role_family="engineering",
        url=f"https://example.com/jobs/{index}",
        collected_at=datetime(2026, 9, 9, tzinfo=UTC),
        content_sha256=f"{index:064x}",
        expected_signal="AI Agent role",
        source_id="fixture",
    )


def _label(index: int) -> GroundTruthLabel:
    return GroundTruthLabel(
        case_id=f"case-{index}",
        title=f"Role {index}",
        company=f"Company {index}",
        location="Remote",
        role_family="engineering",
        required_skills=None,
        hard_constraints=None,
        is_relevant=index == 1,
        recommended_top_k=1 if index == 1 else None,
        annotated_by="reviewer",
        annotated_at=datetime(2026, 9, 18, tzinfo=UTC),
    )


def test_parse_providers_dedupes_and_rejects_unknown():
    assert parse_providers("baseline,llm-demo,snapshot-baseline,baseline") == [
        "baseline",
        "llm-demo",
        "snapshot-baseline",
    ]
    with pytest.raises(ValueError, match="Unsupported"):
        parse_providers("baseline,unknown")


@pytest.mark.asyncio
async def test_runner_reuses_case_order_and_provider_set_per_round(tmp_path: Path):
    calls: list[tuple[str, list[str]]] = []

    async def fake_runner(provider, cases, output_dir):
        calls.append((provider, [case.case_id for case in cases]))
        return [
            JobPrediction(
                case_id=case.case_id,
                title=f"Role {index}",
                company=case.company,
                location="Remote",
                role_family="engineering",
                valid_jobs=1,
                match_score=0.9 if index == 1 else 0.2,
                predicted_relevant=index == 1,
            )
            for index, case in enumerate(cases, start=1)
        ]

    summary = await run_real_job_benchmark(
        dataset_version="real-job-benchmark-v1",
        catalog=[_case(1), _case(2)],
        labels=[_label(1), _label(2)],
        providers=["baseline", "llm-demo"],
        runs=3,
        output_dir=tmp_path,
        runner=fake_runner,
    )

    assert summary.run_count == 6
    assert [provider for provider, _ in calls] == [
        "baseline",
        "llm-demo",
        "baseline",
        "llm-demo",
        "baseline",
        "llm-demo",
    ]
    assert all(case_ids == ["case-1", "case-2"] for _, case_ids in calls)
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "summary.md").exists()
    assert (tmp_path / "summary.html").exists()


@pytest.mark.asyncio
async def test_offline_runner_reads_provider_predictions(tmp_path: Path):
    predictions_path = tmp_path / "predictions.json"
    predictions_path.write_text(
        json.dumps(
            {
                "baseline": [
                    {
                        "case_id": "case-1",
                        "title": "Role 1",
                        "company": "Company 1",
                        "location": "Remote",
                        "role_family": "engineering",
                        "valid_jobs": 1,
                        "match_score": 1.0,
                        "predicted_relevant": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    summary = await run_real_job_benchmark(
        dataset_version="real-job-benchmark-v1",
        catalog=[_case(1)],
        labels=[_label(1)],
        providers=["baseline"],
        runs=1,
        output_dir=tmp_path / "output",
        offline_predictions=predictions_path,
    )

    assert summary.runs[0].source_successes == 1
    assert summary.runs[0].field_metrics.title.accuracy == 1.0


@pytest.mark.asyncio
async def test_snapshot_runner_extracts_from_frozen_page_content(tmp_path: Path):
    snapshots_path = tmp_path / "snapshots.jsonl"
    snapshots_path.write_text(
        json.dumps(
            {
                "case_id": "case-1",
                "url": "https://example.com/jobs/1",
                "title": "Role 1",
                "content": (
                    "Role 1\nCompany 1\nRemote\n"
                    "岗位要求\n"
                    "Python, LangGraph, RAG, Agent\n"
                    "工作内容\n"
                    "Build AI Agent workflows with Python and LangGraph."
                ),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    summary = await run_real_job_benchmark(
        dataset_version="real-job-benchmark-v1",
        catalog=[_case(1)],
        labels=[_label(1)],
        providers=["snapshot-baseline"],
        runs=1,
        output_dir=tmp_path / "output",
        offline_snapshots=snapshots_path,
    )

    assert summary.runs[0].source_successes == 1
    assert summary.runs[0].field_metrics.title.accuracy == 1.0
    assert summary.runs[0].field_metrics.company.accuracy == 1.0
    assert summary.runs[0].recommendation.predicted_relevant >= 1
