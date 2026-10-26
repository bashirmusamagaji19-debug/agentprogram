"""Tests for the C5 model-value scoring and dataset-validation core."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from web_task_agent.model_value_evaluation import (
    CasePrediction,
    DatasetValidationError,
    ModelValueTask,
    canonical_json,
    canonical_sha256,
    freeze_json,
    paired_group_bootstrap,
    read_frozen_json,
    safe_ratio,
    score_case,
    summarize_reader_page_metrics,
    summarize_results,
    task_succeeded,
    validate_dataset,
    write_frozen_json,
)


def make_task(
    *,
    case_id: str = "case-a",
    group_id: str = "group-a",
    split: str = "dev",
    target: int = 2,
    allowed: tuple[str, ...] | None = ("job-1", "job-2"),
    expected: str = "target_reached",
    acceptable: tuple[str, ...] = (),
    refs: tuple[str, ...] = ("snap-a",),
) -> ModelValueTask:
    return ModelValueTask(
        case_id=case_id,
        group_id=group_id,
        split=split,
        profile_id="profile-a",
        conditions={"query": "AI agent intern", "locations": ["Shanghai"]},
        target_count=target,
        allowed_job_keys=None if allowed is None else list(allowed),
        expected_outcome=expected,
        acceptable_stop_reasons=list(acceptable),
        snapshot_refs=list(refs),
    )


def make_prediction(
    *,
    case_id: str = "case-a",
    strategy: str = "rules",
    round_number: int = 1,
    delivered: tuple[str, ...] = ("job-1", "job-2"),
    saved: tuple[str, ...] = ("job-1", "job-2"),
    terminal_status: str = "completed",
    terminal_reason: str = "target_reached",
    planner_calls: int = 1,
    consumed_steps: int = 3,
    elapsed_ms: float = 10.0,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    usage_complete: bool = False,
    unauthorized_writes: int = 0,
    duplicate_writes: int = 0,
    constraint_violations: int = 0,
    error: str = "",
    status: str = "executed",
) -> CasePrediction:
    return CasePrediction(
        case_id=case_id,
        strategy=strategy,
        round=round_number,
        delivered_job_keys=list(delivered),
        saved_job_keys=list(saved),
        terminal_status=terminal_status,
        terminal_reason=terminal_reason,
        planner_calls=planner_calls,
        consumed_steps=consumed_steps,
        elapsed_ms=elapsed_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        usage_complete=usage_complete,
        unauthorized_writes=unauthorized_writes,
        duplicate_writes=duplicate_writes,
        constraint_violations=constraint_violations,
        error=error,
        status=status,
    )


def test_task_is_frozen_and_prediction_is_strict_with_documented_defaults():
    task = make_task()
    with pytest.raises(ValidationError):
        task.case_id = "changed"

    with pytest.raises(ValidationError):
        ModelValueTask(
            case_id="case-x",
            group_id="group-x",
            split="dev",
            profile_id="profile-x",
            conditions={},
            target_count=1,
            allowed_job_keys=[],
            acceptable_stop_reasons=[],
            snapshot_refs=[],
            unexpected="field",
        )

    prediction = make_prediction()
    assert prediction.usage_complete is False
    assert prediction.error == ""
    assert prediction.status == "executed"
    assert make_task().expected_outcome == "target_reached"

    with pytest.raises(ValidationError):
        make_prediction(planner_calls=-1)
    with pytest.raises(ValidationError):
        make_prediction(round_number=0)
    with pytest.raises(ValidationError):
        CasePrediction(
            case_id="case-a",
            strategy="rules",
            round=1,
            delivered_job_keys=[],
            saved_job_keys=[],
            terminal_status="completed",
            terminal_reason="done",
            planner_calls=0,
            consumed_steps=0,
            elapsed_ms=0,
            unauthorized_writes=0,
            duplicate_writes=0,
            constraint_violations=0,
            status="executed",
            unexpected=1,
        )
    with pytest.raises(ValidationError):
        CasePrediction(
            case_id="case-a",
            strategy="rules",
            round=1,
            delivered_job_keys=[],
            saved_job_keys=[],
            terminal_status="completed",
            terminal_reason="done",
            planner_calls=0,
            consumed_steps=0,
            elapsed_ms=0,
            unauthorized_writes=0,
            duplicate_writes=0,
            constraint_violations=0,
        )


def test_task_is_strict_and_public_containers_are_immutable_but_json_serializable():
    allowed = ["job-1", "job-2"]
    task = ModelValueTask(
        case_id="case-immutable",
        group_id="group-immutable",
        split="dev",
        profile_id="profile-immutable",
        conditions={"nested": {"value": 1}, "items": [1, 2]},
        target_count=2,
        allowed_job_keys=allowed,
        acceptable_stop_reasons=["candidate_exhausted"],
        snapshot_refs=["snap-a"],
    )
    allowed.append("job-3")

    assert task.allowed_job_keys == ("job-1", "job-2")
    assert task.acceptable_stop_reasons == ("candidate_exhausted",)
    assert task.snapshot_refs == ("snap-a",)
    with pytest.raises((AttributeError, TypeError)):
        task.allowed_job_keys.append("job-4")
    with pytest.raises(TypeError):
        task.conditions["new"] = 1
    with pytest.raises(TypeError):
        task.conditions["nested"]["value"] = 2
    conditions = task.conditions
    with pytest.raises(TypeError):
        conditions |= {"new": 1}

    dumped = json.loads(task.model_dump_json())
    assert dumped["allowed_job_keys"] == ["job-1", "job-2"]
    assert dumped["conditions"]["nested"] == {"value": 1}
    assert dumped["conditions"]["items"] == [1, 2]
    assert read_frozen_json(freeze_json(task))["case_id"] == "case-immutable"

    with pytest.raises(ValidationError):
        make_task(target=True)


def test_score_case_uses_final_keys_and_reports_invalid_and_missed_counts():
    task = make_task(target=2, allowed=("job-1", "job-2", "job-3"))
    prediction = make_prediction(
        delivered=("job-1", "job-x", "job-2"),
        saved=("job-1", "job-2"),
        terminal_status="failed",
        terminal_reason="model_claimed_failure",
    )

    score = score_case(task, prediction)

    assert score.correct_delivered == 2
    assert score.incorrect_delivered == 1
    assert score.missed == 0
    assert score.precision == pytest.approx(2 / 3)
    assert score.coverage == 1.0
    assert score.task_completed is False
    assert score.expected_outcome_satisfied is False
    assert score.prediction == prediction
    assert score.elapsed_ms == prediction.elapsed_ms
    assert score.terminal_status == "failed"


def test_terminal_self_claim_does_not_override_valid_saved_keys():
    task = make_task(target=2, allowed=("job-1", "job-2"))
    prediction = make_prediction(
        delivered=("job-1", "job-2"),
        saved=("job-1", "job-2"),
        terminal_status="failed",
        terminal_reason="agent_gave_up",
    )

    score = score_case(task, prediction)

    assert score.task_completed is True
    assert score.expected_outcome_satisfied is True
    assert score.precision == 1.0
    assert score.coverage == 1.0


def test_all_refused_has_null_precision_and_correct_refusal_needs_no_writes():
    task = make_task(
        target=1,
        allowed=(),
        expected="refusal",
        acceptable=("no_matching_jobs",),
    )
    refusal = make_prediction(
        delivered=(),
        saved=(),
        terminal_status="refused",
        terminal_reason="no_matching_jobs",
    )

    score = score_case(task, refusal)

    assert score.correct_delivered == 0
    assert score.precision is None
    assert score.coverage is None
    assert score.task_completed is False
    assert score.expected_outcome_satisfied is True

    refused_target = score_case(
        make_task(target=1, allowed=("job-1",)),
        make_prediction(delivered=(), saved=()),
    )
    assert refused_target.precision is None
    assert refused_target.expected_outcome_satisfied is False

    wrote_anyway = score_case(
        task,
        make_prediction(
            delivered=("job-1",),
            saved=("job-1",),
            terminal_status="refused",
            terminal_reason="no_matching_jobs",
        ),
    )
    assert wrote_anyway.expected_outcome_satisfied is False

    wrong_reason = score_case(
        task,
        make_prediction(
            delivered=(),
            saved=(),
            terminal_status="refused",
            terminal_reason="unrelated_reason",
        ),
    )
    assert wrong_reason.expected_outcome_satisfied is False


def test_empty_acceptable_reasons_require_exact_refusal_markers():
    task = make_task(target=1, allowed=(), expected="refusal", acceptable=())
    rejected_status = make_prediction(
        delivered=(),
        saved=(),
        terminal_status="rejected",
        terminal_reason="",
    )
    human_denied = make_prediction(
        delivered=(),
        saved=(),
        terminal_status="completed",
        terminal_reason="human_denied",
    )
    success_like = make_prediction(
        delivered=(),
        saved=(),
        terminal_status="completed",
        terminal_reason="target_reached",
    )
    negated = make_prediction(
        delivered=(),
        saved=(),
        terminal_status="completed",
        terminal_reason="not_rejected",
    )

    assert score_case(task, rejected_status).expected_outcome_satisfied is True
    assert score_case(task, human_denied).expected_outcome_satisfied is True
    assert score_case(task, success_like).expected_outcome_satisfied is False
    assert score_case(task, negated).expected_outcome_satisfied is False
    with pytest.raises(ValidationError, match="success marker"):
        make_task(
            target=1,
            allowed=(),
            expected="refusal",
            acceptable=("target_reached",),
        )

    score = score_case(task, rejected_status)
    assert score.precision is None


def test_semantic_reason_taxonomy_is_enforced_without_rejecting_custom_stop_reasons():
    with pytest.raises(ValidationError, match="success marker"):
        make_task(
            target=2,
            allowed=("job-1", "job-2"),
            expected="partial",
            acceptable=("target_reached",),
        )
    with pytest.raises(ValidationError, match="success marker"):
        make_task(
            target=2,
            allowed=(),
            expected="no_candidates",
            acceptable=("completed",),
        )
    with pytest.raises(ValidationError, match="success marker"):
        make_task(
            target=1,
            allowed=(),
            expected="refusal",
            acceptable=("success",),
        )
    with pytest.raises(ValidationError, match="refusal marker"):
        make_task(
            target=2,
            allowed=(),
            expected="no_candidates",
            acceptable=("rejected",),
        )
    with pytest.raises(ValidationError, match="candidate-shortage marker"):
        make_task(
            target=1,
            allowed=(),
            expected="refusal",
            acceptable=("candidate_exhausted",),
        )

    custom_refusal = make_task(
        target=1,
        allowed=(),
        expected="refusal",
        acceptable=("policy_requires_stop",),
    )
    custom_refusal_score = score_case(
        custom_refusal,
        make_prediction(
            delivered=(),
            saved=(),
            terminal_status="completed",
            terminal_reason="policy_requires_stop",
        ),
    )
    assert custom_refusal_score.expected_outcome_satisfied is True

    partial = make_task(
        target=2,
        allowed=("job-1", "job-2"),
        expected="partial",
        acceptable=(),
    )
    claimed_success = score_case(
        partial,
        make_prediction(
            delivered=("job-1",),
            saved=("job-1",),
            terminal_status="completed",
            terminal_reason="target_reached",
        ),
    )
    assert claimed_success.expected_outcome_satisfied is False

    bypassed = ModelValueTask.model_construct(
        case_id="case-a",
        group_id="group-a",
        split="dev",
        profile_id="profile-a",
        conditions={},
        target_count=1,
        allowed_job_keys=("job-1",),
        expected_outcome="no_candidates",
        acceptable_stop_reasons=("no_candidates",),
        snapshot_refs=(),
    )
    with pytest.raises(ValueError, match="empty gold labels"):
        score_case(bypassed, make_prediction(delivered=(), saved=()))


def test_missing_gold_returns_null_quality_instead_of_zero():
    task = make_task(allowed=None)
    prediction = make_prediction(delivered=("job-1",), saved=("job-1",))

    score = score_case(task, prediction)

    assert score.gold_available is False
    assert score.precision is None
    assert score.coverage is None
    assert score.correct_delivered == 0
    assert score.incorrect_delivered == 0
    assert score.task_completed is False
    assert score.expected_outcome_satisfied is None


def test_refusal_without_gold_is_evaluable_from_writes_and_reason():
    task = make_task(
        target=1,
        allowed=None,
        expected="refusal",
        acceptable=("rejected",),
    )
    prediction = make_prediction(
        delivered=(),
        saved=(),
        terminal_status="rejected",
        terminal_reason="",
    )

    score = score_case(task, prediction)

    assert score.gold_available is False
    assert score.precision is None
    assert score.coverage is None
    assert score.task_completed is False
    assert score.expected_outcome_satisfied is True

    summary = summarize_results([task], [prediction], planned_repeats=1)
    rules = summary["strategies"]["rules"]
    assert rules["target_completion"] == {"numerator": 0, "denominator": 0, "rate": None}
    assert rules["expected_outcome"]["satisfied"] == 1
    assert rules["expected_outcome"]["known"] == 1
    assert rules["delivered"]["precision"] is None


def test_partial_outcome_is_separate_from_full_task_completion():
    task = make_task(
        target=3,
        allowed=("job-1", "job-2", "job-3"),
        expected="partial",
        acceptable=("candidate_exhausted",),
    )
    partial = make_prediction(
        delivered=("job-1", "job-2"),
        saved=("job-1", "job-2"),
        terminal_status="completed",
        terminal_reason="candidate_exhausted",
    )

    score = score_case(task, partial)

    assert score.task_completed is False
    assert score.expected_outcome_satisfied is True
    assert score.correct_delivered == 2
    assert score.missed == 1
    assert score.coverage == pytest.approx(2 / 3)

    full = score_case(
        task,
        make_prediction(delivered=("job-1", "job-2", "job-3"), saved=("job-1", "job-2", "job-3")),
    )
    assert full.task_completed is True
    assert full.expected_outcome_satisfied is False

    empty = score_case(
        task,
        make_prediction(delivered=(), saved=(), terminal_reason="candidate_exhausted"),
    )
    assert empty.expected_outcome_satisfied is False

    no_reason_needed = score_case(
        make_task(
            target=3,
            allowed=("job-1", "job-2", "job-3"),
            expected="partial",
            acceptable=(),
        ),
        make_prediction(
            delivered=("job-1", "job-2"),
            saved=("job-1", "job-2"),
            terminal_status="completed",
            terminal_reason="",
        ),
    )
    assert no_reason_needed.expected_outcome_satisfied is True


def test_no_candidates_outcome_requires_a_clean_explanation_and_no_writes():
    task = make_task(
        target=3,
        allowed=(),
        expected="no_candidates",
        acceptable=("no_candidates",),
    )
    clean = make_prediction(
        delivered=(),
        saved=(),
        terminal_status="completed",
        terminal_reason="no_candidates",
    )

    score = score_case(task, clean)

    assert score.precision is None
    assert score.coverage is None
    assert score.task_completed is False
    assert score.expected_outcome_satisfied is True

    wrong_reason = score_case(
        task,
        make_prediction(
            delivered=(),
            saved=(),
            terminal_status="completed",
            terminal_reason="unexpected",
        ),
    )
    assert wrong_reason.expected_outcome_satisfied is False

    wrote_anyway = score_case(
        task,
        make_prediction(
            delivered=("job-1",),
            saved=("job-1",),
            terminal_status="completed",
            terminal_reason="no_candidates",
        ),
    )
    assert wrote_anyway.expected_outcome_satisfied is False

    empty_accepts_default = score_case(
        make_task(target=2, allowed=(), expected="no_candidates", acceptable=()),
        make_prediction(
            delivered=(),
            saved=(),
            terminal_status="completed",
            terminal_reason="candidates_exhausted",
        ),
    )
    assert empty_accepts_default.expected_outcome_satisfied is True

    negated = score_case(
        make_task(target=2, allowed=(), expected="no_candidates", acceptable=()),
        make_prediction(
            delivered=(),
            saved=(),
            terminal_status="completed",
            terminal_reason="not_no_candidates",
        ),
    )
    assert negated.expected_outcome_satisfied is False


def test_error_and_not_run_episodes_cannot_satisfy_completion():
    task = make_task(target=2, allowed=("job-1", "job-2"))
    for status in ("error", "not_run"):
        prediction = make_prediction(status=status, error="boom" if status == "error" else "")
        score = score_case(task, prediction)
        assert score.task_completed is False
        assert score.expected_outcome_satisfied is False


def test_duplicate_key_entries_and_duplicate_write_counts_are_not_hidden():
    task = make_task(target=2, allowed=("job-1", "job-2"))

    repeated_key = score_case(
        task,
        make_prediction(
            delivered=("job-1", "job-1", "job-2"),
            saved=("job-1", "job-2"),
        ),
    )
    assert repeated_key.duplicate_effects == 1
    assert repeated_key.incorrect_delivered == 1
    assert repeated_key.precision == pytest.approx(2 / 3)
    assert repeated_key.task_completed is False

    double_write = score_case(
        task,
        make_prediction(delivered=("job-1", "job-2"), saved=("job-1", "job-2"), duplicate_writes=2),
    )
    assert double_write.incorrect_delivered == 2
    assert double_write.precision == pytest.approx(2 / 4)
    assert double_write.task_completed is False

    both_lists = score_case(
        task,
        make_prediction(
            delivered=("job-1", "job-1", "job-2"),
            saved=("job-1", "job-2", "job-2"),
        ),
    )
    assert both_lists.duplicate_effects == 2
    assert both_lists.incorrect_delivered == 2
    assert both_lists.precision == pytest.approx(2 / 4)
    assert both_lists.task_completed is False


def test_safe_ratio_returns_none_for_zero_denominator_and_task_succeeded_is_pure():
    assert safe_ratio(1, 0) is None
    assert safe_ratio(0, 5) == 0.0
    assert safe_ratio(3, 4) == 0.75

    assert task_succeeded(
        target=2,
        valid_saved=2,
        constraint_violations=0,
        unauthorized_writes=0,
        duplicate_writes=0,
    )
    assert not task_succeeded(
        target=2,
        valid_saved=2,
        constraint_violations=1,
        unauthorized_writes=0,
        duplicate_writes=0,
    )
    assert not task_succeeded(
        target=2,
        valid_saved=1,
        constraint_violations=0,
        unauthorized_writes=0,
        duplicate_writes=0,
    )


def test_validate_dataset_rejects_duplicate_cases_groups_keys_and_test_leakage():
    with pytest.raises(DatasetValidationError, match="duplicate case_id"):
        validate_dataset([make_task(), make_task()], manifest=None)

    with pytest.raises(DatasetValidationError, match="both dev and test"):
        validate_dataset(
            [
                make_task(case_id="case-a", group_id="shared", split="dev"),
                make_task(case_id="case-b", group_id="shared", split="test"),
            ],
            manifest=None,
        )

    with pytest.raises(DatasetValidationError, match="duplicate allowed_job_key"):
        validate_dataset(
            [make_task(allowed=("job-1", "job-1", "job-2"))],
            manifest=None,
        )


def test_validate_dataset_requires_gold_for_formal_test_when_flagged(tmp_path: Path):
    task = make_task(case_id="case-test", split="test", allowed=None)

    with pytest.raises(DatasetValidationError, match="requires gold"):
        validate_dataset([task], manifest={"formal_test": True})

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"formal_test": True}), encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="requires gold"):
        validate_dataset([task], manifest=manifest_path)

    report = validate_dataset([make_task(case_id="case-test", split="test")], manifest={"formal_test": True})
    assert report["case_count"] == 1


def test_validate_dataset_rejects_unsafe_or_unknown_snapshot_refs():
    for unsafe in ("../outside.json", "/absolute/snapshot.json", "C:/snapshots/a.json", "..\\outside.json"):
        with pytest.raises(DatasetValidationError, match="safe relative POSIX path"):
            validate_dataset([make_task(refs=(unsafe,))], manifest=None)

    with pytest.raises(DatasetValidationError, match="not present in the parent archive"):
        validate_dataset(
            [make_task(refs=("snap-z",))],
            manifest={"snapshots": [{"snapshot_id": "snap-a", "sha256": "a" * 64}]},
        )


def test_validate_dataset_requires_exact_snapshot_reference_identity():
    manifest = {"snapshots": {"safe/snap-a.json": {"sha256": "a" * 64}}}
    assert validate_dataset(
        [make_task(refs=("safe/snap-a.json",))],
        manifest=manifest,
    )["snapshot_ref_count"] == 1

    for ref in ("other/snap-a.json", "safe/snap-a.txt", "snap-a.json"):
        with pytest.raises(DatasetValidationError, match="not present in the parent archive"):
            validate_dataset([make_task(refs=(ref,))], manifest=manifest)


def test_validate_dataset_report_uses_manifest_inventory_and_frozen_manifest(tmp_path: Path):
    manifest = freeze_json(
        {
            "dataset_version": "model-value-v1",
            "formal_test": True,
            "snapshots": [{"snapshot_id": "snap-a", "sha256": "b" * 64}],
        }
    )
    manifest_path = write_frozen_json(tmp_path / "manifest.json", manifest["payload"])

    report = validate_dataset(
        [
            make_task(case_id="case-dev", group_id="group-1", split="dev", refs=("snap-a",)),
            make_task(case_id="case-test", group_id="group-2", split="test", refs=("snap-a",)),
        ],
        manifest=manifest_path,
    )

    assert report["case_count"] == 2
    assert report["group_count"] == 2
    assert report["split_counts"] == {"dev": 1, "test": 1}
    assert report["snapshot_ref_count"] == 2
    assert report["formal_test_checked"] is True


def test_validate_dataset_accepts_snapshot_inventory_shapes_and_checks_archive_files(tmp_path: Path):
    task = make_task(case_id="case-a", refs=("snap-a",))
    for manifest in (
        {"snapshots": {"snap-a": {"sha256": "c" * 64}}},
        {"snapshot_ids": ["snap-a"]},
        {"snapshot_sha256": {"snap-a": "c" * 64}},
    ):
        assert validate_dataset([task], manifest=manifest)["snapshot_ref_count"] == 1

    with pytest.raises(DatasetValidationError, match="64 lowercase hex"):
        validate_dataset(
            [task],
            manifest={"snapshots": {"snap-a": {"sha256": "not-a-digest"}}},
        )
    for malformed_manifest, match in (
        ({"snapshots": [{"sha256": "a" * 64}]}, "missing snapshot id"),
        ({"snapshots": [{"snapshot_id": "snap-a", "sha256": 123}]}, "64 lowercase hex"),
        ({"snapshots": {"snap-a": 123}}, "64 lowercase hex"),
        ({"snapshot_sha256": {"snap-a": 123}}, "64 lowercase hex"),
        ({"snapshot_ids": [None]}, "snapshot id"),
        ({"snapshots": [123]}, "snapshot"),
    ):
        with pytest.raises(DatasetValidationError, match=match):
            validate_dataset([task], manifest=malformed_manifest)

    archive_manifest = {"archive_root": str(tmp_path), "verify_snapshot_files": True}
    with pytest.raises(DatasetValidationError, match="missing from the parent archive"):
        validate_dataset([task], manifest=archive_manifest)

    (tmp_path / "snap-a").write_text("frozen snapshot", encoding="utf-8")
    assert validate_dataset([task], manifest=archive_manifest)["valid"] is True


def test_validate_dataset_rejects_conflicting_hashes_across_inventory_sources():
    task = make_task(case_id="case-a", refs=("snap-a",))
    conflicting_manifests = (
        {
            "snapshots": {"snap-a": {"sha256": "a" * 64}},
            "snapshot_sha256": {"snap-a": "b" * 64},
        },
        {
            "snapshots": [
                {"snapshot_id": "snap-a", "sha256": "a" * 64},
                {"snapshot_id": "snap-a", "sha256": "b" * 64},
            ],
        },
        {
            "snapshot_ids": ["snap-a"],
            "snapshot_hashes": {"snap-a": "a" * 64},
            "snapshots": {"snap-a": {"digest": "b" * 64}},
        },
    )
    for manifest in conflicting_manifests:
        with pytest.raises(DatasetValidationError, match="conflicting sha256"):
            validate_dataset([task], manifest=manifest)

    assert validate_dataset(
        [task],
        manifest={
            "snapshots": [
                {"snapshot_id": "snap-a", "sha256": "a" * 64},
                {"snapshot_id": "snap-a", "digest": "a" * 64},
            ],
        },
    )["valid"] is True


def test_validate_dataset_enforces_semantic_outcome_labels():
    valid_tasks = [
        make_task(case_id="target", group_id="group-target", target=2, allowed=("job-1", "job-2")),
        make_task(
            case_id="partial",
            group_id="group-partial",
            target=3,
            allowed=("job-1", "job-2"),
            expected="partial",
            acceptable=("candidate_exhausted",),
        ),
        make_task(
            case_id="none",
            group_id="group-none",
            target=2,
            allowed=(),
            expected="no_candidates",
            acceptable=("no_candidates",),
        ),
        make_task(
            case_id="refuse",
            group_id="group-refuse",
            target=1,
            allowed=(),
            expected="refusal",
            acceptable=("rejected",),
        ),
    ]
    assert validate_dataset(valid_tasks)["case_count"] == 4

    invalid_cases = (
        (make_task(allowed=None), "target_reached"),
        (make_task(target=3, allowed=("job-1", "job-2")), "target_reached"),
        (
            make_task(
                target=1,
                allowed=("job-1",),
                expected="partial",
                acceptable=("candidate_exhausted",),
            ),
            "partial",
        ),
        (
            make_task(target=1, allowed=(), expected="refusal", acceptable=()),
            "acceptable stop reason",
        ),
        (
            make_task(
                target=1,
                allowed=(),
                expected="refusal",
                acceptable=("rejected", "rejected"),
            ),
            "duplicate acceptable",
        ),
    )
    for task, message in invalid_cases:
        with pytest.raises(DatasetValidationError, match=message):
            validate_dataset([task])

    with pytest.raises(ValidationError, match="empty gold"):
        make_task(
            target=2,
            allowed=("job-1",),
            expected="no_candidates",
            acceptable=("no_candidates",),
        )


def test_canonical_json_and_frozen_round_trip_detect_tampering(tmp_path: Path):
    payload = {"b": [2, 1], "a": {"z": True}}
    canonical = canonical_json(payload)

    assert canonical == '{"a":{"z":true},"b":[2,1]}'
    assert canonical_sha256(payload) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    frozen = freeze_json(payload)
    assert frozen["payload"] == payload
    assert frozen["sha256"] == canonical_sha256(payload)

    path = write_frozen_json(tmp_path / "frozen.json", payload)
    assert read_frozen_json(path) == payload

    tampered = {"payload": {"a": 1}, "sha256": frozen["sha256"]}
    with pytest.raises(ValueError, match="digest"):
        read_frozen_json(tampered)


def test_summarize_results_reports_cohorts_integrity_metrics_and_missing_usage():
    task = make_task(case_id="case-a", group_id="group-a", target=2, allowed=("job-1", "job-2"))
    predictions = [
        make_prediction(
            round_number=1,
            elapsed_ms=10,
            prompt_tokens=40,
            completion_tokens=60,
            total_tokens=100,
            usage_complete=True,
            consumed_steps=3,
            planner_calls=1,
        ),
        make_prediction(
            round_number=2,
            elapsed_ms=20,
            usage_complete=False,
            consumed_steps=5,
            planner_calls=2,
        ),
        make_prediction(
            strategy="always",
            round_number=1,
            delivered=("job-1",),
            saved=("job-1",),
            terminal_status="needs_review",
            terminal_reason="candidate_exhausted",
            elapsed_ms=30,
            consumed_steps=4,
            planner_calls=3,
        ),
        make_prediction(
            strategy="always",
            round_number=2,
            delivered=(),
            saved=(),
            terminal_status="error",
            terminal_reason="timeout",
            status="error",
            error="boom",
            elapsed_ms=40,
            consumed_steps=1,
            planner_calls=4,
        ),
        make_prediction(
            strategy="adaptive",
            round_number=1,
            elapsed_ms=50,
            consumed_steps=2,
            planner_calls=1,
        ),
        make_prediction(
            strategy="adaptive",
            round_number=1,
            elapsed_ms=999,
            consumed_steps=99,
            planner_calls=99,
        ),
        make_prediction(case_id="ghost", round_number=1),
        make_prediction(round_number=3),
    ]

    summary = summarize_results([task], predictions, planned_repeats=2)

    assert summary["planned_repeats"] == 2
    assert summary["planned_cohort"]["cases"] == 1
    assert summary["planned_cohort"]["episodes_per_strategy"] == 2
    assert summary["independent_groups"] == 1

    for strategy in ("rules", "always", "adaptive"):
        assert summary["strategies"][strategy]["planned_episodes"] == 2
        assert summary["strategies"][strategy]["planned_cases"] == 1

    rules = summary["strategies"]["rules"]
    assert rules["observed"] == 2
    assert rules["missing"] == 0
    assert rules["target_completion"] == {"numerator": 2, "denominator": 2, "rate": 1.0}
    assert rules["expected_outcome"]["satisfied"] == 2
    assert rules["expected_outcome"]["known"] == 2
    assert rules["delivered"]["correct"] == 4
    assert rules["delivered"]["incorrect"] == 0
    assert rules["delivered"]["precision"] == 1.0
    assert rules["delivered"]["coverage"] == 1.0
    assert rules["elapsed_ms"]["p50"] == 10
    assert rules["elapsed_ms"]["p95"] == 20
    assert rules["steps"]["total"] == 8
    assert rules["steps"]["p50"] == 3
    assert rules["steps"]["p95"] == 5
    assert rules["observed_tokens"] == 100
    assert rules["usage_missing"] == 1
    assert rules["tokens"]["prompt"] == 40
    assert rules["tokens"]["completion"] == 60
    assert rules["tokens"]["total"] == 100
    assert rules["model_calls"] == 3

    always = summary["strategies"]["always"]
    assert always["observed"] == 2
    assert always["error"] == 1
    assert always["target_completion"] == {"numerator": 0, "denominator": 2, "rate": 0.0}
    assert always["expected_outcome"]["satisfied"] == 0
    assert always["expected_outcome"]["known"] == 2
    assert always["delivered"]["correct"] == 1
    assert always["delivered"]["missed"] == 3
    assert always["delivered"]["coverage"] == pytest.approx(1 / 4)
    assert always["delivered"]["episodes_with_gold"] == 2
    assert always["delivered"]["error_episodes_with_gold"] == 1
    assert always["delivered"]["includes_error_episode_deliveries"] is True
    assert always["error_rate"]["numerator"] == 1
    assert always["error_rate"]["denominator"] == 2
    assert always["error_rate"]["rate"] == 0.5
    assert always["review_rate"]["numerator"] == 1
    assert always["review_rate"]["rate"] == 1.0
    assert always["usage_missing"] == 2
    assert always["observed_tokens"] is None
    assert always["tokens"]["prompt"] is None
    assert always["tokens"]["completion"] is None
    assert always["tokens"]["total"] is None
    assert always["model_calls"] == 7

    adaptive = summary["strategies"]["adaptive"]
    assert adaptive["observed"] == 0
    assert adaptive["missing"] == 2
    assert adaptive["target_completion"] == {"numerator": 0, "denominator": 2, "rate": 0.0}
    assert adaptive["expected_outcome"]["satisfied"] == 0
    assert adaptive["expected_outcome"]["known"] == 2
    assert adaptive["delivered"]["precision"] is None
    assert adaptive["delivered"]["coverage"] is None
    assert adaptive["model_calls"] == 0
    assert adaptive["elapsed_ms"]["p50"] is None

    integrity = summary["integrity"]
    assert integrity["duplicates"] == [
        {"case_id": "case-a", "strategy": "adaptive", "round": 1, "count": 2}
    ]
    assert integrity["unknown_case_ids"] == ["ghost"]
    assert {"case_id": "case-a", "strategy": "adaptive", "round": 1} in integrity["missing_rounds"]
    assert {"case_id": "case-a", "strategy": "adaptive", "round": 2} in integrity["missing_rounds"]
    assert {"case_id": "case-a", "strategy": "rules", "round": 3} in integrity["out_of_range_rounds"]

    assert "fee" not in json.dumps(summary).lower()
    assert "cost" not in json.dumps(summary).lower()


def test_summarize_results_without_predictions_keeps_planned_denominators():
    task = make_task(target=2, allowed=("job-1", "job-2"))

    summary = summarize_results([task], [], planned_repeats=3)

    for strategy in ("rules", "always", "adaptive"):
        result = summary["strategies"][strategy]
        assert result["planned_episodes"] == 3
        assert result["observed"] == 0
        assert result["missing"] == 3
        assert result["target_completion"] == {"numerator": 0, "denominator": 3, "rate": 0.0}
        assert result["expected_outcome"] == {
            "satisfied": 0,
            "known": 3,
            "rate": 0.0,
            "by_outcome": {
                "target_reached": {"satisfied": 0, "known": 3, "rate": 0.0},
            },
        }
        assert result["delivered"]["precision"] is None
        assert result["delivered"]["coverage"] is None
        assert result["elapsed_ms"]["p50"] is None
        assert result["usage_missing"] == 3
        assert result["observed_tokens"] is None
        assert result["tokens"]["prompt"] is None
        assert result["tokens"]["completion"] is None
        assert result["tokens"]["total"] is None
        assert result["tokens"]["usage_missing"] == 3


def test_usage_completeness_requires_all_components_and_unknown_aggregates_are_null():
    task = make_task(target=2, allowed=("job-1", "job-2"))
    predictions = [
        make_prediction(
            round_number=1,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=100,
            usage_complete=True,
        ),
        make_prediction(
            round_number=2,
            prompt_tokens=10,
            completion_tokens=None,
            total_tokens=None,
            usage_complete=False,
        ),
    ]

    summary = summarize_results([task], predictions, planned_repeats=2)
    rules = summary["strategies"]["rules"]

    assert rules["observed"] == 2
    assert rules["observed_tokens"] == 100
    assert rules["tokens"]["prompt"] == 10
    assert rules["tokens"]["completion"] is None
    assert rules["tokens"]["total"] == 100
    assert rules["usage_missing"] == 2


def test_duplicate_episode_is_invalidated_independently_of_input_order():
    task = make_task(target=1, allowed=("job-1",))
    success = make_prediction(
        strategy="adaptive",
        delivered=("job-1",),
        saved=("job-1",),
    )
    failure = make_prediction(strategy="adaptive", delivered=(), saved=())
    rules_success = make_prediction(
        strategy="rules",
        delivered=("job-1",),
        saved=("job-1",),
    )

    success_first = summarize_results([task], [success, failure, rules_success], planned_repeats=1)
    failure_first = summarize_results([task], [failure, success, rules_success], planned_repeats=1)

    assert success_first["strategies"] == failure_first["strategies"]
    adaptive = success_first["strategies"]["adaptive"]
    assert adaptive["observed"] == 0
    assert adaptive["missing"] == 1
    assert adaptive["target_completion"] == {"numerator": 0, "denominator": 1, "rate": 0.0}
    assert success_first["strategies"]["rules"]["target_completion"]["numerator"] == 1

    boot_success_first = paired_group_bootstrap(
        [task],
        [success, failure, rules_success],
        seed=11,
        iterations=20,
        planned_repeats=1,
    )
    boot_failure_first = paired_group_bootstrap(
        [task],
        [failure, success, rules_success],
        seed=11,
        iterations=20,
        planned_repeats=1,
    )
    assert boot_success_first == boot_failure_first
    assert boot_success_first["mean_difference"] == pytest.approx(-1.0)
    assert boot_success_first["integrity"]["duplicates"] == [
        {"case_id": "case-a", "strategy": "adaptive", "round": 1, "count": 2}
    ]


def test_bootstrap_reports_unknown_duplicate_and_out_of_range_cases():
    task = make_task(target=1, allowed=("job-1",))
    predictions = [
        make_prediction(
            strategy="adaptive",
            round_number=1,
            delivered=("job-1",),
            saved=("job-1",),
        ),
        make_prediction(strategy="rules", round_number=1, delivered=(), saved=()),
        make_prediction(
            case_id="ghost",
            strategy="adaptive",
            round_number=1,
            delivered=("job-1",),
            saved=("job-1",),
        ),
        make_prediction(
            strategy="adaptive",
            round_number=4,
            delivered=("job-1",),
            saved=("job-1",),
        ),
        make_prediction(
            strategy="adaptive",
            round_number=2,
            delivered=("job-1",),
            saved=("job-1",),
        ),
        make_prediction(strategy="adaptive", round_number=2, delivered=(), saved=()),
    ]

    result = paired_group_bootstrap(
        [task],
        predictions,
        seed=3,
        iterations=20,
        planned_repeats=2,
    )

    assert result["groups"] == 1
    assert result["paired_episodes"] == 2
    assert result["planned_repeats"] == 2
    assert result["mean_difference"] == pytest.approx(0.5)
    assert result["integrity"]["unknown_case_ids"] == ["ghost"]
    assert result["integrity"]["duplicates"] == [
        {"case_id": "case-a", "strategy": "adaptive", "round": 2, "count": 2}
    ]
    assert {
        "case_id": "case-a",
        "strategy": "adaptive",
        "round": 4,
    } in result["integrity"]["out_of_range_rounds"]
    assert {"case_id": "case-a", "strategy": "adaptive", "round": 2} in result["integrity"][
        "missing_rounds"
    ]
    assert {"case_id": "case-a", "strategy": "rules", "round": 2} in result["integrity"][
        "missing_rounds"
    ]
    assert result["integrity"] == summarize_results(
        [task], predictions, planned_repeats=2
    )["integrity"]


def test_paired_group_bootstrap_resamples_groups_and_is_deterministic():
    tasks = []
    predictions = []
    for index in range(1, 5):
        case_id = f"case-{index}"
        tasks.append(
            make_task(
                case_id=case_id,
                group_id=f"group-{index}",
                target=1,
                allowed=("job-1",),
            )
        )
        for round_number in (1, 2, 3):
            success = index <= 2
            predictions.append(
                make_prediction(
                    case_id=case_id,
                    strategy="adaptive",
                    round_number=round_number,
                    delivered=("job-1",) if success else (),
                    saved=("job-1",) if success else (),
                )
            )
            predictions.append(
                make_prediction(
                    case_id=case_id,
                    strategy="rules",
                    round_number=round_number,
                    delivered=(),
                    saved=(),
                )
            )

    first = paired_group_bootstrap(tasks, predictions, iterations=200, seed=7)
    second = paired_group_bootstrap(tasks, predictions, iterations=200, seed=7)

    assert first == second
    assert first["resampling_unit"] == "group_id"
    assert first["groups"] == 4
    assert first["paired_episodes"] == 12
    assert first["planned_repeats"] == 3
    assert first["mean_difference"] == pytest.approx(0.5)
    assert 0.0 <= first["ci95"][0] <= 0.5 <= first["ci95"][1] <= 1.0

    one_round = [prediction for prediction in predictions if prediction.round == 1]
    without_repeats = paired_group_bootstrap(
        tasks,
        one_round,
        iterations=200,
        seed=7,
        planned_repeats=1,
    )
    assert without_repeats["mean_difference"] == pytest.approx(first["mean_difference"])
    assert without_repeats["ci95"] == first["ci95"]
    assert without_repeats["paired_episodes"] == 4


def test_reader_page_review_rate_uses_attempted_pages_not_task_status():
    metrics = summarize_reader_page_metrics(
        [
            {
                "strategy": "always",
                "reader_counts": {
                    "accepted": 4,
                    "rejected": 0,
                    "review": 10,
                    "error": 0,
                },
            }
        ],
        "always",
    )

    assert metrics["attempted"] == 14
    assert metrics["review_rate"] == {
        "numerator": 10,
        "denominator": 14,
        "rate": pytest.approx(10 / 14),
    }
    assert metrics["error_rate"]["denominator"] == 14


def test_quality_keeps_correct_and_incorrect_deliveries_from_error_episodes():
    task = make_task(target=2, allowed=("job-1", "job-2"))
    prediction = make_prediction(
        delivered=("job-1", "job-3"),
        saved=("job-1", "job-3"),
        terminal_status="error",
        terminal_reason="deadline_exceeded",
        status="error",
        error="TimeoutError",
    )

    summary = summarize_results([task], [prediction], planned_repeats=1)
    rules = summary["strategies"]["rules"]

    assert rules["statuses"]["error"] == 1
    assert rules["delivered"]["correct"] == 1
    assert rules["delivered"]["incorrect"] == 1
    assert rules["delivered"]["missed"] == 1
    assert rules["delivered"]["precision"] == 0.5
    assert rules["delivered"]["coverage"] == 0.5
    assert rules["delivered"]["error_episodes_with_gold"] == 1
