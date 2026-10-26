from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from web_task_agent.page_reliability import PageFailureCategory
from web_task_agent.source_provenance import (
    ArchiveManifest,
    CandidateEvidence,
    ResolutionAttempt,
    SnapshotEvidence,
    candidate_id,
    candidate_identity_sha256,
    snapshot_identity_hash,
)

NOW = datetime(2026, 10, 23, 8, 30, tzinfo=UTC)


def test_operation_identity_is_optional_strict_and_safe():
    fields = dict(candidate_id="candidate-1", sequence=1, strategy="http", started_at=NOW,
                  elapsed_ms=1, status="success")
    assert ResolutionAttempt(**fields).operation_id is None
    assert ResolutionAttempt(**fields).operation_kind is None
    assert ResolutionAttempt(**fields, operation_id="open-1", operation_kind="open").operation_id == "open-1"
    for updates in ({"operation_id": "../unsafe"}, {"operation_id": 2}, {"operation_kind": "fetch"}):
        with pytest.raises(ValidationError):
            ResolutionAttempt(**fields, **updates)


def candidate(**updates):
    values = {
        "candidate_id": "candidate-1",
        "source_spec": "fixture",
        "source_job_id": "job-1",
        "discovery_url": "https://example.com/jobs?page=1",
        "requested_url": "https://example.com/jobs/job-1",
        "canonical_url": "https://example.com/jobs/job-1?lang=en",
        "keyword": "AI intern",
        "location": "Remote",
        "discovered_at": NOW,
        "query_context": {"job_id": "job-1", "page": 1},
    }
    values.update(updates)
    return CandidateEvidence(**values)


def snapshot(**updates):
    values = {
        "snapshot_id": snapshot_identity_hash("b" * 64),
        "candidate_id": "candidate-1",
        "captured_at": NOW,
        "content_origin": "http",
        "requested_url": "https://example.com/jobs/job-1",
        "canonical_url": "https://example.com/jobs/job-1?lang=en",
        "title": "AI Agent Intern",
        "body_relative_path": "bodies/" + "a" * 64 + ".txt",
        "body_sha256": "a" * 64,
        "input_sha256": "b" * 64,
        "evidence_level": "detail",
        "availability": "observed",
        "observed_at": NOW - timedelta(hours=2),
        "cache_age_seconds": 7200,
    }
    values.update(updates)
    return SnapshotEvidence(**values)


def test_models_are_frozen_and_reject_unknown_fields_and_coercion():
    evidence = candidate()
    with pytest.raises(ValidationError):
        evidence.location = "Elsewhere"
    with pytest.raises(ValidationError):
        candidate(headers={"Cookie": "secret"})
    with pytest.raises(ValidationError):
        candidate(schema_version=2)
    with pytest.raises(ValidationError):
        candidate(source_job_id=1)


def test_candidate_id_is_stable_and_prefers_source_native_identity():
    first = candidate_id("fixture", source_job_id="job-1", requested_url="https://a.example/1")
    assert first == candidate_id("fixture", source_job_id="job-1", requested_url="https://b.example/2")
    assert first != candidate_id("fixture", source_job_id="job-2", requested_url="https://a.example/1")
    assert first != candidate_id("other", source_job_id="job-1", requested_url="https://a.example/1")

    canonical = candidate_id(
        "fixture",
        canonical_url="https://example.com/jobs/1?lang=en",
        requested_url="https://example.com/jobs/1",
    )
    requested = candidate_id("fixture", requested_url="https://example.com/jobs/1")
    assert canonical != requested
    assert canonical == candidate_id("fixture", canonical_url="https://example.com/jobs/1?lang=en")
    with pytest.raises(ValueError, match="source_job_id or a URL"):
        candidate_id("fixture")


def test_candidate_identity_is_bound_to_native_identity_and_derived_id():
    explicit = candidate(candidate_id="fixture-1")
    assert len(explicit.identity_sha256) == 64
    assert candidate(candidate_id="fixture-1").identity_sha256 == explicit.identity_sha256

    derived = candidate_id("fixture", source_job_id="job-1")
    derived_identity = candidate(candidate_id=derived).identity_sha256
    assert derived_identity != explicit.identity_sha256
    assert derived_identity == candidate_identity_sha256(
        derived,
        source_spec="fixture",
        source_job_id="job-1",
        requested_url="https://example.com/jobs/job-1",
        canonical_url="https://example.com/jobs/job-1?lang=en",
    )
    with pytest.raises(ValidationError, match="derived candidate_id"):
        candidate(candidate_id=derived, source_job_id="different-job")

    other_native = candidate(candidate_id="fixture-1", source_job_id="job-2")
    assert explicit.identity_sha256 != other_native.identity_sha256


def test_datetime_hash_url_and_public_query_context_validation():
    with pytest.raises(ValidationError, match="timezone-aware"):
        candidate(discovered_at=datetime(2026, 10, 23, 8, 30))
    with pytest.raises(ValidationError, match="credential"):
        candidate(requested_url="https://user:password@example.com/jobs/1")
    with pytest.raises(ValidationError, match="credential"):
        candidate(requested_url="https://example.com/jobs/1?access_token=secret")
    with pytest.raises(ValidationError, match="credential"):
        candidate(requested_url="https://example.com/jobs?q=jobs;access_token=secret")
    with pytest.raises(ValidationError, match="credential"):
        candidate(requested_url="https://example.com/jobs?q=jobs%3Baccess_token%3Dsecret")
    with pytest.raises(ValidationError, match="query_context"):
        candidate(query_context={"headers": "Cookie: secret"})
    with pytest.raises(ValidationError):
        candidate(query_context={"page": True})
    with pytest.raises(ValidationError):
        snapshot(body_sha256="not-a-hash")
    with pytest.raises(ValidationError, match="safe relative"):
        snapshot(body_relative_path="../secret.txt")
    with pytest.raises(ValidationError, match="safe relative"):
        snapshot(body_relative_path="C:/secret.txt")
    with pytest.raises(ValidationError):
        snapshot(cache_age_seconds=-1)


def test_cache_requires_original_observation_and_consistent_declared_age():
    with pytest.raises(ValidationError, match="observed_at"):
        snapshot(content_origin="cache", availability="unknown", observed_at=None)
    with pytest.raises(ValidationError, match="cache_age_seconds"):
        snapshot(content_origin="cache", availability="unknown", cache_age_seconds=None)
    with pytest.raises(ValidationError, match="cache_age_seconds"):
        snapshot(content_origin="cache", availability="unknown", cache_age_seconds=1)


def test_resolution_attempt_and_manifest_keep_public_metadata_only():
    attempt = ResolutionAttempt(
        candidate_id="candidate-1",
        sequence=1,
        strategy="http",
        started_at=NOW,
        elapsed_ms=125,
        status="failure",
        error_category="transient",
        http_status=503,
    )
    assert attempt.error_category == PageFailureCategory.TRANSIENT
    assert attempt.schema_version == 1
    assert "secret" not in attempt.model_dump_json()
    assert (
        ResolutionAttempt(
            candidate_id="candidate-1",
            sequence=2,
            strategy="http",
            started_at=NOW,
            elapsed_ms=3,
            status="failed",
            error_category=PageFailureCategory.TRANSIENT,
        ).status
        == "failed"
    )

    manifest = ArchiveManifest(
        run_id="run-1",
        conditions={"keyword": "AI intern", "location": "Remote"},
        candidate_ids=["candidate-1"],
        snapshot_ids=[snapshot_identity_hash("b" * 64)],
        attempts_path="attempts.jsonl",
        created_at=NOW,
        code_revision="abc123",
        dirty_manifest_sha256="c" * 64,
    )
    assert manifest.schema_version == 1
    with pytest.raises(ValidationError, match="safe relative"):
        ArchiveManifest(
            run_id="run-1",
            conditions={},
            candidate_ids=[],
            snapshot_ids=[],
            attempts_path="../attempts.jsonl",
            created_at=NOW,
            code_revision="abc123",
            dirty_manifest_sha256="c" * 64,
        )
    with pytest.raises(ValidationError, match="safe relative"):
        ArchiveManifest(
            run_id="run-1",
            conditions={},
            candidate_ids=[],
            snapshot_ids=[],
            attempts_path="C:/escape",
            created_at=NOW,
            code_revision="abc123",
            dirty_manifest_sha256="c" * 64,
        )
