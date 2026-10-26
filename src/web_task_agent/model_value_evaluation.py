"""Offline scoring and dataset validation for model-value comparisons.

The functions in this module are deliberately side-effect free: they do not
call a model, fetch a page, or write evaluation data. They consume the final
business result of an episode and human labels, so a model's terminal claim
cannot turn an incomplete or unsafe run into a success.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import random
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "CasePrediction",
    "CaseScore",
    "DatasetValidationError",
    "ModelValueTask",
    "canonical_json",
    "canonical_sha256",
    "freeze_json",
    "paired_group_bootstrap",
    "read_frozen_json",
    "safe_ratio",
    "score_case",
    "summarize_reader_page_metrics",
    "summarize_results",
    "task_succeeded",
    "validate_dataset",
    "verify_frozen_json",
    "write_frozen_json",
]

Split = Literal["dev", "test"]
ExpectedOutcome = Literal["target_reached", "partial", "refusal", "no_candidates"]
Strategy = Literal["rules", "always", "adaptive"]
PredictionStatus = Literal["executed", "error", "not_run"]

STRATEGIES: tuple[Strategy, ...] = ("rules", "always", "adaptive")
_REFUSAL_MARKERS = frozenset(
    {
        "approval_rejected",
        "declined",
        "denied",
        "human_denied",
        "human_rejected",
        "refusal",
        "refused",
        "rejected",
    }
)
_NO_CANDIDATE_TOKENS = frozenset(
    {
        "candidate_exhausted",
        "candidate_shortage",
        "candidates_exhausted",
        "no_candidate",
        "no_candidates",
        "no_candidates_found",
        "no_results",
        "none_found",
        "zero_candidates",
    }
)
_SUCCESS_MARKERS = frozenset(
    {
        "complete",
        "completed",
        "done",
        "ok",
        "success",
        "succeeded",
        "target_reached",
    }
)
_FORMAL_GOLD_FLAGS = (
    "formal",
    "formal_test",
    "require_gold",
    "require_gold_for_test",
    "require_gold_for_formal_test",
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _normalize_marker(value: str) -> str:
    return re.sub(r"[\s-]+", "_", value.strip().casefold())


def _reason_category(value: str) -> str | None:
    normalized = _normalize_marker(value)
    if normalized in _SUCCESS_MARKERS:
        return "success"
    if normalized in _REFUSAL_MARKERS:
        return "refusal"
    if normalized in _NO_CANDIDATE_TOKENS:
        return "candidate_shortage"
    return None


def _task_semantic_label_issues(task: Any) -> list[str]:
    """Return unconditional label contradictions that must never be scored.

    Custom stop reasons remain valid. Only known markers from a different
    outcome family, success markers, and nonempty gold on no-candidate tasks
    are rejected here; dataset-wide requirements stay in validate_dataset.
    """

    issues: list[str] = []
    expected = task.expected_outcome
    if (
        expected == "no_candidates"
        and task.allowed_job_keys is not None
        and len(set(task.allowed_job_keys)) > 0
    ):
        issues.append(f"no_candidates case {task.case_id!r} requires empty gold labels")

    if expected == "target_reached":
        return issues

    for reason in task.acceptable_stop_reasons:
        if not isinstance(reason, str) or not reason.strip():
            continue
        category = _reason_category(reason)
        if category == "success":
            issues.append(
                f"acceptable stop reason {reason!r} is a success marker for expected outcome {expected!r}"
            )
        elif category == "refusal" and expected != "refusal":
            issues.append(
                f"acceptable stop reason {reason!r} is a refusal marker for expected outcome {expected!r}"
            )
        elif category == "candidate_shortage" and expected == "refusal":
            issues.append(
                f"acceptable stop reason {reason!r} is a candidate-shortage marker for expected outcome {expected!r}"
            )
    return issues


class FrozenDict(dict[str, Any]):
    """A JSON-serializable dict that rejects public mutation."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        dict.__init__(self)
        for key, value in dict(*args, **kwargs).items():
            dict.__setitem__(self, key, _deep_freeze(value))

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError("frozen mapping is immutable")

    def __delitem__(self, key: str) -> None:
        raise TypeError("frozen mapping is immutable")

    def clear(self) -> None:
        raise TypeError("frozen mapping is immutable")

    def pop(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("frozen mapping is immutable")

    def popitem(self) -> Any:
        raise TypeError("frozen mapping is immutable")

    def setdefault(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("frozen mapping is immutable")

    def update(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("frozen mapping is immutable")

    def __ior__(self, other: Any) -> FrozenDict:
        raise TypeError("frozen mapping is immutable")

    def __copy__(self) -> FrozenDict:
        return FrozenDict(self)

    def __deepcopy__(self, memo: dict[int, Any]) -> FrozenDict:
        return FrozenDict(
            {key: copy.deepcopy(value, memo) for key, value in self.items()}
        )

    def __reduce__(self) -> tuple[Any, tuple[dict[str, Any]]]:
        return (FrozenDict, (dict(self),))


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenDict(value)
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


class DatasetValidationError(ValueError):
    """Raised when a frozen evaluation dataset violates its invariants."""

    def __init__(self, issues: Sequence[str]) -> None:
        self.issues = list(issues)
        super().__init__("; ".join(self.issues))


class ModelValueTask(BaseModel):
    """One frozen planner or reader task shown to every strategy."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    case_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    split: Split
    profile_id: str = Field(min_length=1)
    conditions: dict[str, Any] = Field(default_factory=dict)
    target_count: int = Field(ge=1)
    allowed_job_keys: tuple[str, ...] | None = None
    expected_outcome: ExpectedOutcome = "target_reached"
    acceptable_stop_reasons: tuple[str, ...] = ()
    snapshot_refs: tuple[str, ...] = ()

    @field_validator("allowed_job_keys", mode="before")
    @classmethod
    def _copy_allowed_job_keys(cls, value: Any) -> tuple[str, ...] | None:
        if value is None:
            return None
        if not isinstance(value, (list, tuple)):
            raise ValueError("allowed_job_keys must be a sequence of job keys")
        return tuple(value)

    @field_validator("acceptable_stop_reasons", "snapshot_refs", mode="before")
    @classmethod
    def _copy_text_tuple(cls, value: Any) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("value must be a sequence of strings")
        return tuple(value)

    @field_validator("conditions", mode="before")
    @classmethod
    def _freeze_conditions(cls, value: Any) -> FrozenDict:
        if not isinstance(value, Mapping):
            raise ValueError("conditions must be a mapping")
        return FrozenDict(value)

    @model_validator(mode="after")
    def _freeze_public_conditions(self) -> ModelValueTask:
        object.__setattr__(self, "conditions", FrozenDict(self.conditions))
        return self

    @model_validator(mode="after")
    def _enforce_semantic_labels(self) -> ModelValueTask:
        issues = _task_semantic_label_issues(self)
        if issues:
            raise ValueError("; ".join(issues))
        return self


class CasePrediction(BaseModel):
    """Final observable result and telemetry for one strategy attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    case_id: str = Field(min_length=1)
    strategy: Strategy
    round: int = Field(ge=1)
    delivered_job_keys: tuple[str, ...]
    saved_job_keys: tuple[str, ...]
    terminal_status: str
    terminal_reason: str
    planner_calls: int = Field(ge=0)
    consumed_steps: int = Field(ge=0)
    elapsed_ms: float = Field(ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    usage_complete: bool = False
    unauthorized_writes: int = Field(ge=0)
    duplicate_writes: int = Field(ge=0)
    constraint_violations: int = Field(ge=0)
    error: str = ""
    status: PredictionStatus

    @field_validator("delivered_job_keys", "saved_job_keys", mode="before")
    @classmethod
    def _copy_job_keys(cls, value: Any) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("job keys must be a sequence of strings")
        return tuple(value)


class CaseScore(BaseModel):
    """Label-based score for one episode, retaining all original telemetry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    group_id: str
    split: Split
    strategy: Strategy
    round: int
    expected_outcome: ExpectedOutcome

    correct_delivered: int
    incorrect_delivered: int
    missed: int
    precision: float | None
    coverage: float | None
    task_completed: bool
    expected_outcome_satisfied: bool | None
    gold_available: bool

    delivered_count: int
    saved_count: int
    delivered_job_keys: tuple[str, ...]
    saved_job_keys: tuple[str, ...]
    correct_saved: int
    invalid_saved: int
    duplicate_effects: int
    counts: dict[str, int]

    terminal_status: str
    terminal_reason: str
    status: PredictionStatus
    error: str
    usage_complete: bool
    planner_calls: int
    consumed_steps: int
    elapsed_ms: float
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    unauthorized_writes: int
    duplicate_writes: int
    constraint_violations: int
    prediction: CasePrediction

    @model_validator(mode="after")
    def _freeze_counts(self) -> CaseScore:
        object.__setattr__(self, "counts", FrozenDict(self.counts))
        return self


def _to_jsonable(value: Any) -> Any:
    """Convert supported values to deterministic JSON-compatible objects."""

    if isinstance(value, BaseModel):
        return _to_jsonable(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            output[key] = _to_jsonable(item)
        return output
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(payload: Any) -> str:
    """Return compact, sorted, UTF-8 JSON used for stable digests."""

    return json.dumps(
        _to_jsonable(payload),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(payload: Any) -> str:
    """Return the SHA-256 digest of canonical_json(payload)."""

    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def freeze_json(payload: Any) -> dict[str, Any]:
    """Wrap a payload and its canonical digest in a frozen envelope."""

    body = _to_jsonable(payload)
    return {"payload": body, "sha256": canonical_sha256(body)}


def verify_frozen_json(envelope: Mapping[str, Any]) -> Any:
    """Verify a frozen envelope and return a deep copy of its payload."""

    if not isinstance(envelope, Mapping) or "payload" not in envelope or "sha256" not in envelope:
        raise ValueError("frozen JSON envelope requires payload and sha256")
    expected = canonical_sha256(envelope["payload"])
    actual = envelope["sha256"]
    if not isinstance(actual, str) or not hmac.compare_digest(actual, expected):
        raise ValueError("frozen JSON digest does not match payload")
    return copy.deepcopy(envelope["payload"])


def read_frozen_json(source: str | Path | Mapping[str, Any]) -> Any:
    """Read a frozen JSON envelope from a path or mapping and verify it."""

    if isinstance(source, Mapping):
        return verify_frozen_json(source)
    payload = json.loads(Path(source).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("frozen JSON root must be an object")
    return verify_frozen_json(payload)


def write_frozen_json(path: str | Path, payload: Any) -> Path:
    """Atomically write a frozen JSON envelope and return its path."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.tmp")
    temporary.write_text(canonical_json(freeze_json(payload)), encoding="utf-8")
    temporary.replace(destination)
    return destination


def safe_ratio(numerator: int, denominator: int) -> float | None:
    """Return numerator / denominator, or None when it is undefined."""

    return numerator / denominator if denominator else None


def task_succeeded(
    *,
    target: int,
    valid_saved: int,
    constraint_violations: int,
    unauthorized_writes: int,
    duplicate_writes: int,
) -> bool:
    """Pure success predicate for a target-reaching planner episode."""

    return (
        valid_saved >= target
        and constraint_violations == 0
        and unauthorized_writes == 0
        and duplicate_writes == 0
    )


def _as_task(task: ModelValueTask | Mapping[str, Any]) -> ModelValueTask:
    if isinstance(task, ModelValueTask):
        return task
    return ModelValueTask.model_validate(task)


def _as_prediction(prediction: CasePrediction | Mapping[str, Any]) -> CasePrediction:
    if isinstance(prediction, CasePrediction):
        return prediction
    return CasePrediction.model_validate(prediction)


def _reason_matches(task: ModelValueTask, prediction: CasePrediction, *, no_candidates: bool) -> bool:
    if _reason_category(prediction.terminal_reason) == "success":
        return False
    if task.acceptable_stop_reasons:
        accepted = set(task.acceptable_stop_reasons)
        matches = [
            value
            for value in (prediction.terminal_reason, prediction.terminal_status)
            if value in accepted
        ]
        return any(_normalize_marker(value) not in _SUCCESS_MARKERS for value in matches)
    reason = _normalize_marker(prediction.terminal_reason)
    if no_candidates:
        return reason in _NO_CANDIDATE_TOKENS
    status = _normalize_marker(prediction.terminal_status)
    return status in _REFUSAL_MARKERS or reason in _REFUSAL_MARKERS


def score_case(
    task: ModelValueTask | Mapping[str, Any],
    prediction: CasePrediction | Mapping[str, Any],
) -> CaseScore:
    """Score one episode by its final keys and observable side effects."""

    task = _as_task(task)
    prediction = _as_prediction(prediction)
    if task.case_id != prediction.case_id:
        raise ValueError(f"prediction case_id {prediction.case_id!r} does not match task {task.case_id!r}")
    label_issues = _task_semantic_label_issues(task)
    if label_issues:
        raise ValueError("invalid task semantic label: " + "; ".join(label_issues))

    delivered = list(prediction.delivered_job_keys)
    saved = list(prediction.saved_job_keys)
    delivered_set = set(delivered)
    saved_set = set(saved)
    delivered_duplicates = len(delivered) - len(delivered_set)
    saved_duplicates = len(saved) - len(saved_set)
    duplicate_effects = max(prediction.duplicate_writes, delivered_duplicates + saved_duplicates)

    gold_available = task.allowed_job_keys is not None
    allowed = set(task.allowed_job_keys or [])

    if gold_available:
        correct_delivered = len(delivered_set & allowed)
        incorrect_delivered = len(delivered_set - allowed) + duplicate_effects
        correct_saved = len(saved_set & allowed)
        invalid_saved = len(saved_set - allowed)
        opportunity = min(task.target_count, len(allowed))
        missed = max(task.target_count - correct_delivered, 0)
        precision = safe_ratio(correct_delivered, correct_delivered + incorrect_delivered)
        coverage = safe_ratio(correct_delivered, opportunity)
    else:
        correct_delivered = 0
        incorrect_delivered = 0
        correct_saved = 0
        invalid_saved = 0
        missed = 0
        precision = None
        coverage = None

    clean = (
        prediction.status == "executed"
        and not prediction.error.strip()
        and prediction.constraint_violations == 0
        and prediction.unauthorized_writes == 0
        and duplicate_effects == 0
        and incorrect_delivered == 0
        and invalid_saved == 0
    )
    task_completed = bool(
        gold_available
        and clean
        and task_succeeded(
            target=task.target_count,
            valid_saved=correct_saved,
            constraint_violations=prediction.constraint_violations,
            unauthorized_writes=prediction.unauthorized_writes,
            duplicate_writes=duplicate_effects,
        )
    )
    no_business_writes = (
        not delivered_set
        and not saved_set
        and duplicate_effects == 0
        and prediction.unauthorized_writes == 0
    )

    expected_outcome_satisfied: bool | None
    if not clean:
        expected_outcome_satisfied = False
    elif task.expected_outcome == "target_reached":
        expected_outcome_satisfied = task_completed if gold_available else None
    elif task.expected_outcome == "partial":
        if not gold_available:
            expected_outcome_satisfied = None
        else:
            reason_ok = (
                prediction.terminal_reason in set(task.acceptable_stop_reasons)
                if task.acceptable_stop_reasons
                else True
            )
            reason_category = _reason_category(prediction.terminal_reason)
            expected_outcome_satisfied = (
                0 < correct_saved < task.target_count
                and reason_ok
                and reason_category not in {"refusal", "success"}
            )
    elif task.expected_outcome == "refusal":
        expected_outcome_satisfied = no_business_writes and _reason_matches(
            task, prediction, no_candidates=False
        )
    else:
        expected_outcome_satisfied = no_business_writes and _reason_matches(
            task, prediction, no_candidates=True
        )

    counts = {
        "delivered": len(delivered_set),
        "saved": len(saved_set),
        "correct_delivered": correct_delivered,
        "incorrect_delivered": incorrect_delivered,
        "missed": missed,
        "correct_saved": correct_saved,
        "invalid_saved": invalid_saved,
        "duplicate_effects": duplicate_effects,
        "planner_calls": prediction.planner_calls,
        "consumed_steps": prediction.consumed_steps,
        "unauthorized_writes": prediction.unauthorized_writes,
        "duplicate_writes": prediction.duplicate_writes,
        "constraint_violations": prediction.constraint_violations,
    }
    return CaseScore(
        case_id=task.case_id,
        group_id=task.group_id,
        split=task.split,
        strategy=prediction.strategy,
        round=prediction.round,
        expected_outcome=task.expected_outcome,
        correct_delivered=correct_delivered,
        incorrect_delivered=incorrect_delivered,
        missed=missed,
        precision=precision,
        coverage=coverage,
        task_completed=task_completed,
        expected_outcome_satisfied=expected_outcome_satisfied,
        gold_available=gold_available,
        delivered_count=len(delivered_set),
        saved_count=len(saved_set),
        delivered_job_keys=tuple(delivered),
        saved_job_keys=tuple(saved),
        correct_saved=correct_saved,
        invalid_saved=invalid_saved,
        duplicate_effects=duplicate_effects,
        counts=counts,
        terminal_status=prediction.terminal_status,
        terminal_reason=prediction.terminal_reason,
        status=prediction.status,
        error=prediction.error,
        usage_complete=prediction.usage_complete,
        planner_calls=prediction.planner_calls,
        consumed_steps=prediction.consumed_steps,
        elapsed_ms=prediction.elapsed_ms,
        prompt_tokens=prediction.prompt_tokens,
        completion_tokens=prediction.completion_tokens,
        total_tokens=prediction.total_tokens,
        unauthorized_writes=prediction.unauthorized_writes,
        duplicate_writes=prediction.duplicate_writes,
        constraint_violations=prediction.constraint_violations,
        prediction=prediction,
    )


def _manifest_payload(manifest: Mapping[str, Any] | str | Path | None) -> dict[str, Any]:
    if manifest is None:
        return {}
    if isinstance(manifest, BaseModel):
        payload: Any = manifest.model_dump(mode="json")
    elif isinstance(manifest, Mapping):
        payload = dict(manifest)
    else:
        payload = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("evaluation manifest must be a JSON object")
    if "payload" in payload and "sha256" in payload:
        verified = verify_frozen_json(payload)
        if not isinstance(verified, Mapping):
            raise ValueError("evaluation manifest payload must be a JSON object")
        return dict(verified)
    return dict(payload)


def _valid_snapshot_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _record_digest(snapshot_id: str, value: Any, issues: list[str]) -> str | None:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        issues.append(f"snapshot {snapshot_id!r} sha256 must be 64 lowercase hex characters")
        return None
    return value


def _entry_digest(snapshot_id: str, entry: Mapping[str, Any], issues: list[str]) -> str | None:
    digest: str | None = None
    for key in ("sha256", "digest", "content_sha256"):
        if key not in entry:
            continue
        candidate = _record_digest(snapshot_id, entry[key], issues)
        if candidate is None:
            continue
        if digest is not None and digest != candidate:
            issues.append(f"snapshot {snapshot_id!r} has conflicting sha256 values")
        elif digest is None:
            digest = candidate
    return digest


def _set_inventory_digest(
    inventory: dict[str, str | None],
    snapshot_id: str,
    digest: str | None,
    issues: list[str],
    conflict_ids: set[str],
) -> None:
    if snapshot_id not in inventory:
        inventory[snapshot_id] = digest
        return
    existing = inventory[snapshot_id]
    if digest is None:
        return
    if existing is None:
        inventory[snapshot_id] = digest
        return
    if existing != digest and snapshot_id not in conflict_ids:
        issues.append(f"snapshot {snapshot_id!r} has conflicting sha256 values")
        conflict_ids.add(snapshot_id)


def _snapshot_inventory(manifest: Mapping[str, Any]) -> tuple[dict[str, str | None], bool, list[str]]:
    inventory: dict[str, str | None] = {}
    supplied = False
    issues: list[str] = []
    conflict_ids: set[str] = set()

    raw_snapshots = manifest.get("snapshots")
    if raw_snapshots is not None:
        supplied = True
        if isinstance(raw_snapshots, Mapping):
            for raw_snapshot_id, value in raw_snapshots.items():
                snapshot_id = str(raw_snapshot_id)
                if not _valid_snapshot_id(snapshot_id):
                    issues.append("snapshot id must be a non-empty string")
                    if isinstance(value, Mapping):
                        _entry_digest("<missing>", value, issues)
                    elif value is not None:
                        _record_digest("<missing>", value, issues)
                    continue
                if value is None:
                    _set_inventory_digest(
                        inventory, snapshot_id, None, issues, conflict_ids
                    )
                elif isinstance(value, Mapping):
                    _set_inventory_digest(
                        inventory,
                        snapshot_id,
                        _entry_digest(snapshot_id, value, issues),
                        issues,
                        conflict_ids,
                    )
                else:
                    _set_inventory_digest(
                        inventory,
                        snapshot_id,
                        _record_digest(snapshot_id, value, issues),
                        issues,
                        conflict_ids,
                    )
        elif isinstance(raw_snapshots, list):
            for item in raw_snapshots:
                if isinstance(item, str):
                    if not _valid_snapshot_id(item):
                        issues.append("snapshot id must be a non-empty string")
                        continue
                    _set_inventory_digest(inventory, item, None, issues, conflict_ids)
                elif isinstance(item, Mapping):
                    snapshot_id = (
                        item.get("snapshot_id")
                        or item.get("id")
                        or item.get("ref")
                        or item.get("path")
                    )
                    if not _valid_snapshot_id(snapshot_id):
                        issues.append("snapshot entry is missing snapshot id")
                        _entry_digest("<missing>", item, issues)
                        continue
                    normalized_id = str(snapshot_id)
                    _set_inventory_digest(
                        inventory,
                        normalized_id,
                        _entry_digest(normalized_id, item, issues),
                        issues,
                        conflict_ids,
                    )
                else:
                    issues.append("snapshot entry is missing snapshot id")
        else:
            issues.append("manifest snapshots must be a list or mapping")

    raw_ids = manifest.get("snapshot_ids")
    if raw_ids is not None:
        supplied = True
        if not isinstance(raw_ids, list):
            issues.append("manifest snapshot_ids must be a list")
        else:
            for snapshot_id in raw_ids:
                if not _valid_snapshot_id(snapshot_id):
                    issues.append("snapshot id must be a non-empty string")
                    continue
                _set_inventory_digest(
                    inventory, str(snapshot_id), None, issues, conflict_ids
                )

    for key in ("snapshot_sha256", "snapshot_hashes"):
        raw_hashes = manifest.get(key)
        if raw_hashes is None:
            continue
        supplied = True
        if not isinstance(raw_hashes, Mapping):
            issues.append(f"manifest {key} must be a mapping")
            continue
        for snapshot_id, digest in raw_hashes.items():
            snapshot_key = str(snapshot_id)
            if not _valid_snapshot_id(snapshot_key):
                issues.append("snapshot id must be a non-empty string")
                _record_digest("<missing>", digest, issues)
                continue
            _set_inventory_digest(
                inventory,
                snapshot_key,
                _record_digest(snapshot_key, digest, issues),
                issues,
                conflict_ids,
            )

    return inventory, supplied, issues


def _snapshot_ref_is_safe(ref: Any) -> bool:
    if not isinstance(ref, str) or not ref.strip():
        return False
    if "\\" in ref or "://" in ref:
        return False
    if ref.startswith("/"):
        return False
    if re.match(r"^[A-Za-z]:", ref):
        return False
    raw_parts = [part for part in ref.split("/") if part]
    if not raw_parts or any(part in {".", ".."} for part in raw_parts):
        return False
    path = PurePosixPath(ref)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        return False
    return True


def _ref_matches_inventory(ref: str, inventory: Mapping[str, str | None]) -> bool:
    ref_path = str(PurePosixPath(ref))
    return any(
        ref_path == str(PurePosixPath(known))
        for known in inventory
    )


def validate_dataset(
    tasks: Sequence[ModelValueTask | Mapping[str, Any]],
    manifest: Mapping[str, Any] | str | Path | None = None,
) -> dict[str, Any]:
    """Validate frozen task invariants, splits, labels, and snapshot refs."""

    task_list = [_as_task(task) for task in tasks]
    manifest_data = _manifest_payload(manifest)
    inventory, inventory_supplied, inventory_issues = _snapshot_inventory(manifest_data)
    formal_test = any(manifest_data.get(flag) is True for flag in _FORMAL_GOLD_FLAGS)
    archive_root_value = manifest_data.get("archive_root") or manifest_data.get("parent_archive")
    archive_root = Path(str(archive_root_value)).resolve() if archive_root_value else None
    verify_files = manifest_data.get("verify_snapshot_files") is True

    issues: list[str] = list(inventory_issues)
    seen_case_ids: set[str] = set()
    group_splits: dict[str, set[str]] = {}
    snapshot_ref_count = 0
    split_counts: Counter[str] = Counter()

    for task in task_list:
        issues.extend(_task_semantic_label_issues(task))
        split_counts[task.split] += 1
        if task.case_id in seen_case_ids:
            issues.append(f"duplicate case_id: {task.case_id}")
        seen_case_ids.add(task.case_id)
        group_splits.setdefault(task.group_id, set()).add(task.split)

        if formal_test and task.split == "test" and task.allowed_job_keys is None:
            issues.append(
                f"test case {task.case_id!r} requires gold labels (allowed_job_keys is None)"
            )

        if task.allowed_job_keys is not None:
            seen_keys: set[str] = set()
            for job_key in task.allowed_job_keys:
                if not isinstance(job_key, str) or not job_key.strip():
                    issues.append(f"empty allowed_job_key in case {task.case_id!r}")
                elif job_key in seen_keys:
                    issues.append(
                        f"duplicate allowed_job_key {job_key!r} in case {task.case_id!r}"
                    )
                else:
                    seen_keys.add(job_key)
            unique_gold_count: int | None = len(seen_keys)
        else:
            unique_gold_count = None

        reasons = list(task.acceptable_stop_reasons)
        if any(not isinstance(reason, str) or not reason.strip() for reason in reasons):
            issues.append(f"empty acceptable stop reason in case {task.case_id!r}")
        if len(set(reasons)) != len(reasons):
            issues.append(f"duplicate acceptable stop reason in case {task.case_id!r}")
        if task.expected_outcome != "target_reached" and not reasons:
            issues.append(
                f"case {task.case_id!r} requires at least one acceptable stop reason "
                f"for expected_outcome {task.expected_outcome!r}"
            )

        if task.expected_outcome == "target_reached":
            if unique_gold_count is None:
                issues.append(f"target_reached case {task.case_id!r} requires gold job keys")
            elif unique_gold_count < task.target_count:
                issues.append(
                    f"target_reached case {task.case_id!r} requires at least "
                    f"{task.target_count} gold job keys"
                )
        elif task.expected_outcome == "partial":
            if unique_gold_count is None:
                issues.append(f"partial case {task.case_id!r} requires gold job keys")
            elif not 1 <= unique_gold_count < task.target_count:
                issues.append(
                    f"partial case {task.case_id!r} requires between 1 and "
                    f"{task.target_count - 1} gold job keys"
                )
        elif task.expected_outcome == "no_candidates":
            if unique_gold_count != 0:
                issues.append(
                    f"no_candidates case {task.case_id!r} requires empty gold labels"
                )

        for ref in task.snapshot_refs:
            snapshot_ref_count += 1
            if not _snapshot_ref_is_safe(ref):
                issues.append(
                    f"snapshot ref {ref!r} in case {task.case_id!r} must be a safe relative POSIX path"
                )
                continue
            if inventory_supplied and not _ref_matches_inventory(ref, inventory):
                issues.append(
                    f"snapshot ref {ref!r} in case {task.case_id!r} is not present in the parent archive manifest"
                )
                continue
            if archive_root is not None:
                target = (archive_root / PurePosixPath(ref)).resolve()
                if not target.is_relative_to(archive_root):
                    issues.append(
                        f"snapshot ref {ref!r} in case {task.case_id!r} escapes the parent archive"
                    )
                elif verify_files and not target.is_file():
                    issues.append(
                        f"snapshot ref {ref!r} in case {task.case_id!r} is missing from the parent archive"
                    )

    for group_id, splits in group_splits.items():
        if len(splits) > 1:
            issues.append(f"group {group_id!r} appears in both dev and test splits")

    if issues:
        raise DatasetValidationError(issues)

    source_reproducibility = manifest_data.get("source_code_reproducibility")
    source_reproducibility = (
        dict(source_reproducibility)
        if isinstance(source_reproducibility, Mapping)
        else {}
    )
    return {
        "valid": True,
        "dataset_valid": True,
        "code_version_match": source_reproducibility.get("current_match"),
        "source_hash_semantics": source_reproducibility.get(
            "semantics",
            "historical_build_record_not_reproducibility_proof",
        ),
        "case_count": len(task_list),
        "group_count": len(group_splits),
        "split_counts": {"dev": split_counts["dev"], "test": split_counts["test"]},
        "snapshot_ref_count": snapshot_ref_count,
        "parent_archive_snapshots": len(inventory) if inventory_supplied else 0,
        "formal_test_checked": formal_test,
    }


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _rate_block(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {"numerator": numerator, "denominator": denominator, "rate": safe_ratio(numerator, denominator)}


def _mean_or_none(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _sum_known(values: Sequence[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _usage_is_complete(prediction: CasePrediction) -> bool:
    return (
        prediction.usage_complete
        and prediction.prompt_tokens is not None
        and prediction.completion_tokens is not None
        and prediction.total_tokens is not None
    )


def summarize_reader_page_metrics(
    details: Sequence[Mapping[str, Any]],
    strategy: str,
) -> dict[str, Any]:
    """Count Reader outcomes per attempted page, independent of task status."""

    totals: Counter[str] = Counter()
    for detail in details:
        if detail.get("strategy") != strategy:
            continue
        counts = detail.get("reader_counts")
        if not isinstance(counts, Mapping):
            continue
        for name in ("accepted", "rejected", "review", "error"):
            value = counts.get(name, 0)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                totals[name] += value
    attempted = sum(totals.values())
    return {
        "counts": {name: totals[name] for name in ("accepted", "rejected", "review", "error")},
        "attempted": attempted,
        "review_rate": _rate_block(totals["review"], attempted),
        "error_rate": _rate_block(totals["error"], attempted),
    }


def summarize_results(
    tasks: Sequence[ModelValueTask | Mapping[str, Any]],
    predictions: Sequence[CasePrediction | Mapping[str, Any]],
    planned_repeats: int = 3,
) -> dict[str, Any]:
    """Summarize all strategies over one identical planned case/round cohort."""

    if isinstance(planned_repeats, bool) or not isinstance(planned_repeats, int) or planned_repeats < 1:
        raise ValueError("planned_repeats must be a positive integer")

    task_list = [_as_task(task) for task in tasks]
    validate_dataset(task_list)
    task_by_id = {task.case_id: task for task in task_list}
    prediction_list = [_as_prediction(prediction) for prediction in predictions]

    grouped: dict[tuple[str, str, int], list[CasePrediction]] = {}
    for prediction in prediction_list:
        key = (prediction.case_id, prediction.strategy, prediction.round)
        grouped.setdefault(key, []).append(prediction)
    duplicate_counts: Counter[tuple[str, str, int]] = Counter(
        {key: len(items) - 1 for key, items in grouped.items() if len(items) > 1}
    )

    valid_index: dict[tuple[str, str, int], CasePrediction] = {}
    unknown_case_ids: set[str] = set()
    out_of_range_rounds: list[dict[str, Any]] = []
    for key, items in grouped.items():
        case_id, strategy, round_number = key
        if case_id not in task_by_id:
            unknown_case_ids.add(case_id)
            continue
        if not 1 <= round_number <= planned_repeats:
            out_of_range_rounds.append(
                {"case_id": case_id, "strategy": strategy, "round": round_number}
            )
            continue
        if len(items) == 1:
            valid_index[key] = items[0]

    planned_episodes = len(task_list) * planned_repeats
    strategies: dict[str, Any] = {}
    outcomes_in_dataset: list[ExpectedOutcome] = []
    for task in task_list:
        if task.expected_outcome not in outcomes_in_dataset:
            outcomes_in_dataset.append(task.expected_outcome)

    for strategy in STRATEGIES:
        strategy_predictions = [
            prediction
            for (case_id, prediction_strategy, _round), prediction in valid_index.items()
            if prediction_strategy == strategy
        ]
        scores_by_key = {
            key: score_case(task_by_id[key[0]], prediction)
            for key, prediction in valid_index.items()
            if key[1] == strategy
        }
        observed = len(strategy_predictions)
        status_counts = Counter(prediction.status for prediction in strategy_predictions)
        statuses = {name: status_counts[name] for name in sorted(status_counts)}

        target_numerator = 0
        target_denominator = 0
        outcome_satisfied = 0
        outcome_known = 0
        by_outcome: dict[str, dict[str, int | float | None]] = {
            outcome: {"satisfied": 0, "known": 0, "rate": None} for outcome in outcomes_in_dataset
        }

        for task in task_list:
            if task.allowed_job_keys is None and task.expected_outcome in {
                "target_reached",
                "partial",
            }:
                continue
            for round_number in range(1, planned_repeats + 1):
                key = (task.case_id, strategy, round_number)
                score = scores_by_key.get(key)
                satisfied = bool(score is not None and score.expected_outcome_satisfied is True)
                if task.expected_outcome == "target_reached":
                    target_denominator += 1
                    if score is not None and score.task_completed:
                        target_numerator += 1
                outcome_known += 1
                outcome_satisfied += int(satisfied)
                outcome_block = by_outcome[task.expected_outcome]
                outcome_block["known"] = int(outcome_block["known"]) + 1
                outcome_block["satisfied"] = int(outcome_block["satisfied"]) + int(satisfied)

        for outcome in by_outcome:
            block = by_outcome[outcome]
            block["rate"] = safe_ratio(int(block["satisfied"]), int(block["known"]))

        quality_scores = [
            score
            for score in scores_by_key.values()
            if score.gold_available
        ]
        correct_delivered = sum(score.correct_delivered for score in quality_scores)
        incorrect_delivered = sum(score.incorrect_delivered for score in quality_scores)
        missed = sum(score.missed for score in quality_scores)
        opportunity = sum(
            min(task_by_id[score.case_id].target_count, len(set(task_by_id[score.case_id].allowed_job_keys or [])))
            for score in quality_scores
        )
        delivered_block = {
            "correct": correct_delivered,
            "incorrect": incorrect_delivered,
            "missed": missed,
            "precision": safe_ratio(correct_delivered, correct_delivered + incorrect_delivered),
            "coverage": safe_ratio(correct_delivered, opportunity),
            "episodes_with_gold": len(quality_scores),
            "error_episodes_with_gold": sum(
                1
                for score in quality_scores
                if score.status == "error" or bool(score.error.strip())
            ),
            "includes_error_episode_deliveries": True,
        }

        attempted = [
            prediction for prediction in strategy_predictions if prediction.status != "not_run"
        ]
        elapsed_values = [float(prediction.elapsed_ms) for prediction in attempted]
        step_values = [float(prediction.consumed_steps) for prediction in attempted]
        error_numerator = sum(
            1
            for prediction in attempted
            if prediction.status == "error" or bool(prediction.error.strip())
        )
        error_denominator = len(attempted)

        review_known = [
            prediction
            for prediction in strategy_predictions
            if prediction.status == "executed" and prediction.terminal_status.strip()
        ]
        review_numerator = sum(
            1
            for prediction in review_known
            if "review" in prediction.terminal_status.casefold()
            or "review" in prediction.terminal_reason.casefold()
        )

        token_predictions = strategy_predictions
        prompt_tokens = _sum_known([prediction.prompt_tokens for prediction in token_predictions])
        completion_tokens = _sum_known(
            [prediction.completion_tokens for prediction in token_predictions]
        )
        total_tokens = _sum_known([prediction.total_tokens for prediction in token_predictions])
        complete_usage = sum(1 for prediction in token_predictions if _usage_is_complete(prediction))
        usage_missing = max(planned_episodes - complete_usage, 0)
        elapsed_p50 = _nearest_rank(elapsed_values, 50)
        elapsed_p95 = _nearest_rank(elapsed_values, 95)
        steps_total = int(sum(step_values))
        steps_mean = _mean_or_none(step_values)

        strategies[strategy] = {
            "planned_cases": len(task_list),
            "planned_repeats": planned_repeats,
            "planned_episodes": planned_episodes,
            "planned": planned_episodes,
            "independent_cases": len(task_list),
            "independent_groups": len({task.group_id for task in task_list}),
            "observed": observed,
            "missing": max(planned_episodes - observed, 0),
            "executed": status_counts["executed"],
            "error": status_counts["error"],
            "not_run": status_counts["not_run"],
            "statuses": statuses,
            "status_counts": statuses,
            "target_completion": _rate_block(target_numerator, target_denominator),
            "expected_outcome": {
                "satisfied": outcome_satisfied,
                "known": outcome_known,
                "rate": safe_ratio(outcome_satisfied, outcome_known),
                "by_outcome": by_outcome,
            },
            "delivered": delivered_block,
            "correct_delivered": correct_delivered,
            "incorrect_delivered": incorrect_delivered,
            "missed": missed,
            "precision": delivered_block["precision"],
            "coverage": delivered_block["coverage"],
            "review_rate": _rate_block(review_numerator, len(review_known)),
            "error_rate": _rate_block(error_numerator, error_denominator),
            "steps": {
                "total": steps_total,
                "mean": steps_mean,
                "p50": _nearest_rank(step_values, 50),
                "p95": _nearest_rank(step_values, 95),
            },
            "steps_total": steps_total,
            "steps_mean": steps_mean,
            "elapsed_ms": {
                "count": len(elapsed_values),
                "p50": elapsed_p50,
                "p95": elapsed_p95,
            },
            "elapsed_p50": elapsed_p50,
            "elapsed_p95": elapsed_p95,
            "tokens": {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": total_tokens,
                "observed_total": total_tokens,
                "usage_missing": usage_missing,
            },
            "observed_tokens": total_tokens,
            "usage_missing": usage_missing,
            "model_calls": sum(prediction.planner_calls for prediction in strategy_predictions),
            "planner_calls": sum(prediction.planner_calls for prediction in strategy_predictions),
        }

    missing_rounds = [
        {"case_id": task.case_id, "strategy": strategy, "round": round_number}
        for strategy in STRATEGIES
        for task in task_list
        for round_number in range(1, planned_repeats + 1)
        if (task.case_id, strategy, round_number) not in valid_index
    ]
    duplicates = [
        {
            "case_id": case_id,
            "strategy": strategy,
            "round": round_number,
            "count": duplicate_counts[(case_id, strategy, round_number)] + 1,
        }
        for case_id, strategy, round_number in sorted(duplicate_counts)
    ]

    out_of_range_sorted = sorted(
        out_of_range_rounds,
        key=lambda item: (item["case_id"], item["strategy"], item["round"]),
    )
    return {
        "planned_repeats": planned_repeats,
        "planned_cohort": {
            "cases": len(task_list),
            "groups": len({task.group_id for task in task_list}),
            "repeats": planned_repeats,
            "episodes_per_strategy": planned_episodes,
        },
        "independent_cases": len(task_list),
        "independent_groups": len({task.group_id for task in task_list}),
        "strategies": strategies,
        "duplicates": duplicates,
        "unknown_case_ids": sorted(unknown_case_ids),
        "missing_rounds": missing_rounds,
        "out_of_range_rounds": out_of_range_sorted,
        "integrity": {
            "duplicates": duplicates,
            "duplicate_count": sum(duplicate_counts.values()),
            "unknown_case_ids": sorted(unknown_case_ids),
            "missing_rounds": missing_rounds,
            "out_of_range_rounds": out_of_range_sorted,
        },
    }


def paired_group_bootstrap(
    tasks: Sequence[ModelValueTask | Mapping[str, Any]],
    predictions: Sequence[CasePrediction | Mapping[str, Any]],
    left: str = "adaptive",
    right: str = "rules",
    seed: int = 20261023,
    iterations: int = 2000,
    planned_repeats: int = 3,
) -> dict[str, Any]:
    """Estimate a paired strategy difference with group-level resampling.

    The independent unit is group_id. Repeats and multiple variants in a
    group are averaged into the group value before resampling.
    """

    if left == right:
        raise ValueError("left and right strategies must differ")
    if left not in STRATEGIES or right not in STRATEGIES:
        raise ValueError("left and right must be rules, always, or adaptive")
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 1:
        raise ValueError("iterations must be a positive integer")
    if (
        isinstance(planned_repeats, bool)
        or not isinstance(planned_repeats, int)
        or planned_repeats < 1
    ):
        raise ValueError("planned_repeats must be a positive integer")

    task_list = [_as_task(task) for task in tasks]
    validate_dataset(task_list)
    task_by_id = {task.case_id: task for task in task_list}
    prediction_list = [_as_prediction(prediction) for prediction in predictions]

    grouped: dict[tuple[str, str, int], list[CasePrediction]] = {}
    for prediction in prediction_list:
        key = (prediction.case_id, prediction.strategy, prediction.round)
        grouped.setdefault(key, []).append(prediction)
    duplicate_counts: Counter[tuple[str, str, int]] = Counter(
        {key: len(items) - 1 for key, items in grouped.items() if len(items) > 1}
    )

    valid_index: dict[tuple[str, str, int], CasePrediction] = {}
    unknown_case_ids: set[str] = set()
    out_of_range_rounds: list[dict[str, Any]] = []
    for key, items in grouped.items():
        case_id, strategy, round_number = key
        if case_id not in task_by_id:
            unknown_case_ids.add(case_id)
            continue
        if not 1 <= round_number <= planned_repeats:
            out_of_range_rounds.append(
                {"case_id": case_id, "strategy": strategy, "round": round_number}
            )
            continue
        if len(items) == 1:
            valid_index[key] = items[0]

    group_values: dict[str, dict[str, dict[tuple[str, int], float]]] = {}
    for task in task_list:
        if task.allowed_job_keys is None and task.expected_outcome in {
            "target_reached",
            "partial",
        }:
            continue
        group_strategy = group_values.setdefault(task.group_id, {left: {}, right: {}})
        for strategy in (left, right):
            for round_number in range(1, planned_repeats + 1):
                key = (task.case_id, strategy, round_number)
                prediction = valid_index.get(key)
                if prediction is None:
                    value = 0.0
                else:
                    score = score_case(task, prediction)
                    value = 1.0 if score.expected_outcome_satisfied is True else 0.0
                group_strategy.setdefault(strategy, {})[(task.case_id, round_number)] = value

    all_groups = sorted({task.group_id for task in task_list})
    paired_differences: list[float] = []
    left_group_means: list[float] = []
    right_group_means: list[float] = []
    excluded_groups: list[str] = []
    paired_episodes = 0

    for group_id in all_groups:
        values = group_values.get(group_id, {left: {}, right: {}})
        left_values = values.get(left, {})
        right_values = values.get(right, {})
        episode_keys = sorted(set(left_values) | set(right_values))
        if not episode_keys:
            excluded_groups.append(group_id)
            continue
        left_mean = sum(left_values.get(episode_key, 0.0) for episode_key in episode_keys) / len(episode_keys)
        right_mean = sum(right_values.get(episode_key, 0.0) for episode_key in episode_keys) / len(episode_keys)
        left_group_means.append(left_mean)
        right_group_means.append(right_mean)
        paired_differences.append(left_mean - right_mean)
        paired_episodes += len(episode_keys)

    mean_difference = _mean_or_none(paired_differences)
    if paired_differences:
        rng = random.Random(seed)
        bootstrap_means: list[float] = []
        group_count = len(paired_differences)
        for _ in range(iterations):
            total = 0.0
            for _ in range(group_count):
                total += paired_differences[rng.randrange(group_count)]
            bootstrap_means.append(total / group_count)
        bootstrap_means.sort()
        low_index = min(iterations - 1, int(0.025 * iterations))
        high_index = min(iterations - 1, max(0, math.ceil(0.975 * iterations) - 1))
        ci95: list[float | None] = [bootstrap_means[low_index], bootstrap_means[high_index]]
    else:
        ci95 = [None, None]

    missing_rounds = [
        {"case_id": task.case_id, "strategy": strategy, "round": round_number}
        for strategy in STRATEGIES
        for task in task_list
        for round_number in range(1, planned_repeats + 1)
        if (task.case_id, strategy, round_number) not in valid_index
    ]
    duplicates = [
        {
            "case_id": case_id,
            "strategy": strategy,
            "round": round_number,
            "count": duplicate_counts[(case_id, strategy, round_number)] + 1,
        }
        for case_id, strategy, round_number in sorted(duplicate_counts)
    ]
    out_of_range_sorted = sorted(
        out_of_range_rounds,
        key=lambda item: (item["case_id"], item["strategy"], item["round"]),
    )
    integrity = {
        "duplicates": duplicates,
        "duplicate_count": sum(duplicate_counts.values()),
        "unknown_case_ids": sorted(unknown_case_ids),
        "missing_rounds": missing_rounds,
        "out_of_range_rounds": out_of_range_sorted,
    }

    return {
        "left": left,
        "right": right,
        "metric": "expected_outcome_satisfied",
        "resampling_unit": "group_id",
        "seed": seed,
        "iterations": iterations,
        "planned_repeats": planned_repeats,
        "groups": len(paired_differences),
        "paired_episodes": paired_episodes,
        "excluded_groups": excluded_groups,
        "mean_difference": mean_difference,
        "left_mean": _mean_or_none(left_group_means),
        "right_mean": _mean_or_none(right_group_means),
        "ci95": ci95,
        "ci95_low": ci95[0],
        "ci95_high": ci95[1],
        "duplicates": duplicates,
        "unknown_case_ids": sorted(unknown_case_ids),
        "missing_rounds": missing_rounds,
        "out_of_range_rounds": out_of_range_sorted,
        "integrity": integrity,
    }
