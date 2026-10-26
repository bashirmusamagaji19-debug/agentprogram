import hashlib
import json
import multiprocessing
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from web_task_agent.extractor import PageExtractor
from web_task_agent.models import BrowserPage
from web_task_agent.page_reliability import PageFailure, PageFailureCategory
from web_task_agent.source_archive import (
    ArchiveConflictError,
    ArchiveCorruptionError,
    ArchiveNotFoundError,
    ArchiveSchemaError,
    SourceArchive,
)
from web_task_agent.source_provenance import (
    ArchiveManifest,
    CandidateEvidence,
    candidate_id,
    candidate_identity_sha256,
    candidate_record_sha256,
)

NOW = datetime(2026, 10, 23, 8, 30, tzinfo=UTC)
URL = "https://example.com/jobs/1"


def observation(*, captured_at=NOW, observed_at=NOW - timedelta(days=2), origin="http"):
    cache_age = int((captured_at - observed_at).total_seconds()) if origin == "cache" else None
    return {
        "captured_at": captured_at,
        "observed_at": observed_at,
        "content_origin": origin,
        "canonical_url": "https://example.com/jobs/1?lang=en",
        "evidence_level": "detail",
        "availability": "unknown" if origin == "cache" else "observed",
        "cache_age_seconds": cache_age,
    }


def page(content="Title: AI Agent Intern\nCompany: 示例公司\nLocation: Remote\n正文：构建 Agent。"):
    return BrowserPage(
        url=URL,
        title="AI Agent Intern",
        content=content,
        source="http",
    )


def new_archive(tmp_path, *, root=None):
    archive = SourceArchive(root or (tmp_path / "run-archive"))
    cid = candidate_id("fixture", source_job_id="1")
    archive.write_candidate(
        CandidateEvidence(
            candidate_id=cid,
            source_spec="fixture",
            source_job_id="1",
            discovery_url="https://example.com/jobs?page=1",
            requested_url=URL,
            canonical_url="https://example.com/jobs/1?lang=en",
            keyword="AI intern",
            location="Remote",
            discovered_at=NOW,
            title="AI Agent Intern",
            company="示例公司",
            query_context={"page": 1, "job_id": "1"},
        )
    )
    return archive, cid


def test_atomic_snapshot_write_supports_long_windows_archive_paths(tmp_path):
    archive_root = tmp_path / ("a" * 60) / ("b" * 60)
    final_body_length = len(
        str(archive_root / "bodies" / f"{'x' * 64}.txt")
    )
    legacy_temp_length = len(
        str(archive_root / "snapshots" / f".{'x' * 64}.json.{'0' * 8}.tmp")
    )
    assert final_body_length > 260
    assert legacy_temp_length > 260

    archive, cid = new_archive(tmp_path, root=archive_root)

    snapshot_id = archive.write_page(cid, page(), observation())
    assert archive.read_page(snapshot_id).url == URL


def manifest_for(cid, snapshot_ids, *, code_revision="deadbeef"):
    return ArchiveManifest(
        run_id="run-1",
        conditions={"keyword": "AI intern", "location": "Remote"},
        candidate_ids=[cid],
        snapshot_ids=snapshot_ids,
        attempts_path="attempts.jsonl",
        created_at=NOW,
        code_revision=code_revision,
        dirty_manifest_sha256=hashlib.sha256(b"dirty").hexdigest(),
    )


def _append_failures_in_child(root, cid, count):
    archive = SourceArchive(root)
    for index in range(count):
        archive.write_failure(
            cid,
            {
                "strategy": "http",
                "started_at": datetime(2026, 10, 23, 8, 30, tzinfo=UTC),
                "elapsed_ms": index,
                "category": "transient",
            },
        )


def _increment_counter_in_child(root, count):
    archive = SourceArchive(root)
    counter = root / "counter.txt"
    for _ in range(count):
        with archive._locked():
            value = int(counter.read_text(encoding="ascii")) if counter.exists() else 0
            time.sleep(0.002)
            counter.write_text(str(value + 1), encoding="ascii")


def _append_success_in_child(root, cid, snapshot_id, count):
    archive = SourceArchive(root)
    for index in range(count):
        archive.append_attempt(
            {
                "candidate_id": cid,
                "strategy": "http",
                "started_at": datetime(2026, 10, 23, 8, 30, tzinfo=UTC),
                "elapsed_ms": index,
                "status": "success",
                "snapshot_id": snapshot_id,
            }
        )


def test_exact_utf8_body_round_trip_and_old_observed_time(tmp_path):
    archive, cid = new_archive(tmp_path)
    original = page("正文第一行\r\n正文第二行\n特殊字符：\u0000 é 🚀")
    snapshot_id = archive.write_page(cid, original, observation(origin="cache"))

    raw = original.content.encode("utf-8")
    body_path = tmp_path / "run-archive" / "bodies" / (hashlib.sha256(raw).hexdigest() + ".txt")
    assert body_path.read_bytes() == raw
    snapshot_payload = json.loads(
        (tmp_path / "run-archive" / "snapshots" / f"{snapshot_id}.json").read_text(encoding="utf-8")
    )
    assert snapshot_payload["body_sha256"] == hashlib.sha256(raw).hexdigest()
    assert snapshot_payload["observed_at"].startswith("2026-10-21T08:30:00")

    loaded = SourceArchive(tmp_path / "run-archive").read_page(snapshot_id)
    assert loaded.content.encode("utf-8") == raw
    assert loaded.title == original.title
    assert loaded.url == URL
    assert loaded.metadata["snapshot_id"] == snapshot_id
    assert loaded.metadata["candidate_id"] == cid
    assert loaded.metadata["observed_at"] == "2026-10-21T08:30:00Z"
    assert loaded.metadata["availability"] == "unknown"


def test_snapshot_identity_changes_for_body_title_source_or_time(tmp_path):
    archive, cid = new_archive(tmp_path)
    first = archive.write_page(cid, page(), observation())
    same = archive.write_page(cid, page(), observation())
    different_body = archive.write_page(cid, page("Title: AI Agent Intern\n完全不同"), observation())
    different_title = archive.write_page(
        cid,
        page().model_copy(update={"title": "另一标题"}),
        observation(),
    )
    different_source = archive.write_page(cid, page(), observation(origin="list"))
    different_time = archive.write_page(
        cid,
        page(),
        observation(observed_at=NOW - timedelta(days=3)),
    )
    assert len({first, same, different_body, different_title, different_source, different_time}) == 5
    assert first == same


def test_same_body_for_different_candidates_uses_distinct_snapshot_ids(tmp_path):
    archive, cid = new_archive(tmp_path)
    second = candidate_id("fixture", source_job_id="2")
    archive.write_candidate(
        CandidateEvidence(
            candidate_id=second,
            source_spec="fixture",
            source_job_id="2",
            requested_url=URL,
            keyword="AI intern",
            location="Remote",
            discovered_at=NOW,
        )
    )
    first_snapshot = archive.write_page(cid, page(), observation())
    second_snapshot = archive.write_page(second, page(), observation())
    assert first_snapshot != second_snapshot
    assert len(list((tmp_path / "run-archive" / "bodies").glob("*.txt"))) == 1
    assert archive.read_page(first_snapshot).metadata["candidate_id"] == cid
    assert archive.read_page(second_snapshot).metadata["candidate_id"] == second


def test_snapshot_restores_exact_candidate_context_after_candidate_update(tmp_path):
    archive = SourceArchive(tmp_path / "run-archive")
    cid = "fixture-context"
    original_candidate = CandidateEvidence(
        candidate_id=cid,
        source_spec="fixture",
        source_job_id="native-1",
        requested_url=URL,
        canonical_url="https://example.com/jobs/1?lang=en",
        keyword="AI intern",
        location="Remote",
        discovered_at=NOW,
        company="Hint Company",
        location_hint="Remote Hint",
        title="Hinted Title",
    )
    archive.write_candidate(original_candidate)
    original_page = BrowserPage(
        url=URL,
        title="AI Agent Intern",
        content=(
            "AI Agent Intern\nAmbiguous unlabeled header\n\n"
            "Requirements: Python LangGraph\nResponsibilities: Build agents"
        ),
        source="aggregator:fixture",
        metadata={
            "discovered_company": "Hint Company",
            "discovered_location": "Remote Hint",
            "discovered_title": "Hinted Title",
            "canonical_url": "https://example.com/jobs/1?lang=en",
            "tier": "tier-a",
            "source_spec": "fixture",
            "source_job_id": "native-1",
            "content_origin": "http",
        },
    )
    snapshot_id = archive.write_page(cid, original_page, observation())

    archive.write_candidate(
        original_candidate.model_copy(
            update={
                "company": "Later Company",
                "location_hint": "Later Location",
                "canonical_url": "https://example.com/jobs/1?lang=zh",
            }
        )
    )

    loaded = archive.read_page(snapshot_id)
    assert loaded.metadata["discovered_company"] == "Hint Company"
    assert loaded.metadata["discovered_location"] == "Remote Hint"
    assert loaded.metadata["discovered_title"] == "Hinted Title"
    assert loaded.metadata["canonical_url"] == "https://example.com/jobs/1?lang=en"
    assert loaded.metadata["tier"] == "tier-a"
    assert loaded.metadata["source_spec"] == "fixture"
    assert loaded.metadata["source_job_id"] == "native-1"
    assert loaded.source == "aggregator:fixture"
    assert loaded.metadata["candidate_record_sha256"] == candidate_record_sha256(original_candidate)
    extracted = PageExtractor().extract(loaded)
    assert extracted.company == "Hint Company"
    assert extracted.source == "aggregator:fixture"
    assert extracted.location == "Remote Hint"
    assert extracted.tier == "tier-a"
    assert extracted.url == "https://example.com/jobs/1?lang=en"


def test_page_context_cannot_override_bound_candidate_identity(tmp_path):
    archive, cid = new_archive(tmp_path)
    conflicting = page().model_copy(
        update={"metadata": {"source_spec": "other-source", "source_job_id": "native-2"}}
    )

    with pytest.raises(ArchiveConflictError, match="candidate|source_spec|source_job"):
        archive.write_page(cid, conflicting, observation())


def test_cached_snapshot_preserves_original_page_origin(tmp_path):
    archive, cid = new_archive(tmp_path)
    cached_page = page().model_copy(
        update={
            "source": "aggregator:fixture",
            "metadata": {"content_origin": "official-api"},
        }
    )

    snapshot_id = archive.write_page(cid, cached_page, observation(origin="cache"))
    loaded = archive.read_page(snapshot_id)

    assert loaded.source == "aggregator:fixture"
    assert loaded.metadata["content_origin"] == "official-api"
    assert loaded.metadata["snapshot_content_origin"] == "cache"
    assert loaded.metadata["availability"] == "unknown"


def test_non_cached_snapshot_rejects_conflicting_page_origin(tmp_path):
    archive, cid = new_archive(tmp_path)
    conflicting = page().model_copy(update={"metadata": {"content_origin": "list"}})

    with pytest.raises(ArchiveConflictError, match="content_origin"):
        archive.write_page(cid, conflicting, observation(origin="http"))


@pytest.mark.parametrize("legacy_origin", ["jd_text-fallback", "detail-probed"])
def test_legacy_page_origin_aliases_validate_as_list_and_remain_visible(tmp_path, legacy_origin):
    archive, cid = new_archive(tmp_path)
    legacy_page = page().model_copy(update={"metadata": {"content_origin": legacy_origin}})

    snapshot_id = archive.write_page(cid, legacy_page, observation(origin="list"))
    loaded = archive.read_page(snapshot_id)

    assert loaded.metadata["content_origin"] == legacy_origin
    assert loaded.metadata["snapshot_content_origin"] == "list"


def test_duplicate_city_references_and_failures_are_append_only(tmp_path):
    archive, cid = new_archive(tmp_path)
    archive.write_candidate(
        CandidateEvidence(
            candidate_id=cid,
            source_spec="fixture",
            source_job_id="1",
            requested_url=URL,
            keyword="AI intern",
            location="Shanghai",
            discovered_at=NOW,
        )
    )
    archive.write_page(
        cid,
        page().model_copy(update={"metadata": {"discovered_company": "示例公司"}}),
        observation(),
    )
    relative = archive.write_failure(
        cid,
        {
            "strategy": "http",
            "started_at": NOW,
            "elapsed_ms": 31,
            "http_status": 503,
            "category": "transient",
            "detail": "Cookie: should-never-be-saved",
        },
    )
    archive.write_failure(cid, PageFailure(PageFailureCategory.REMOVED, "private response body"))

    candidate_rows = [
        json.loads(line)
        for line in (tmp_path / "run-archive" / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {row["location"] for row in candidate_rows} == {"Remote", "Shanghai"}
    attempts_raw = (tmp_path / "run-archive" / relative).read_text(encoding="utf-8")
    attempts = [json.loads(line) for line in attempts_raw.splitlines()]
    assert [row["sequence"] for row in attempts] == [1, 2]
    assert [row["error_category"] for row in attempts] == ["transient", "removed"]
    assert "should-never-be-saved" not in attempts_raw
    assert "private response body" not in attempts_raw
    assert len({row["snapshot_id"] for row in attempts}) == 1


def test_same_candidate_id_cannot_change_native_source_identity(tmp_path):
    archive, _cid = new_archive(tmp_path)
    with pytest.raises(ValidationError, match="native source identity"):
        archive.write_candidate(
            CandidateEvidence(
                candidate_id="fixture-1",
                source_spec="fixture",
                source_job_id="native-1",
                requested_url=URL,
                keyword="AI intern",
                location="Remote",
                discovered_at=NOW,
            ).model_copy(update={"source_job_id": "native-2"})
        )
    with pytest.raises(ArchiveConflictError, match="native identity"):
        archive.write_candidate(
            CandidateEvidence(
                candidate_id="fixture-1",
                source_spec="fixture",
                source_job_id="native-1",
                requested_url=URL,
                keyword="AI intern",
                location="Remote",
                discovered_at=NOW,
            )
        )
        archive.write_candidate(
            CandidateEvidence(
                candidate_id="fixture-1",
                source_spec="fixture",
                source_job_id="native-2",
                requested_url=URL,
                keyword="AI intern",
                location="Shanghai",
                discovered_at=NOW,
            )
        )


@pytest.mark.parametrize("mutation", ["body", "snapshot_hash", "unknown_schema"])
def test_tampering_and_unknown_schema_are_rejected(tmp_path, mutation):
    archive, cid = new_archive(tmp_path)
    snapshot_id = archive.write_page(cid, page(), observation())
    snapshot_path = tmp_path / "run-archive" / "snapshots" / f"{snapshot_id}.json"
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if mutation == "body":
        (tmp_path / "run-archive" / payload["body_relative_path"]).write_text("tampered", encoding="utf-8")
    elif mutation == "snapshot_hash":
        payload["input_sha256"] = "0" * 64
        snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        payload["schema_version"] = 99
        snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises((ArchiveCorruptionError, ArchiveSchemaError)):
        archive.read_page(snapshot_id)


@pytest.mark.parametrize(
    "mutation",
    [
        "candidate",
        "requested_url",
        "canonical_url",
        "availability",
        "evidence_level",
        "content_origin",
        "cache_age_seconds",
        "captured_at",
        "observed_at",
        "source_context",
    ],
)
def test_snapshot_metadata_tampering_is_rejected_by_read_and_verify(tmp_path, mutation):
    archive, cid = new_archive(tmp_path)
    second = candidate_id("fixture", source_job_id="2")
    archive.write_candidate(
        CandidateEvidence(
            candidate_id=second,
            source_spec="fixture",
            source_job_id="2",
            requested_url=URL,
            keyword="AI intern",
            location="Remote",
            discovered_at=NOW,
        )
    )
    snapshot_id = archive.write_page(cid, page(), observation())
    archive.write_manifest(manifest_for(cid, [snapshot_id]).model_copy(update={"candidate_ids": [cid, second]}))
    snapshot_path = tmp_path / "run-archive" / "snapshots" / f"{snapshot_id}.json"
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if mutation == "candidate":
        payload["candidate_id"] = second
    elif mutation == "requested_url":
        payload["requested_url"] = "https://example.com/jobs/1?lang=en"
    elif mutation == "canonical_url":
        payload["canonical_url"] = "https://example.com/jobs/1?lang=zh"
    elif mutation == "availability":
        payload["availability"] = "unknown"
    elif mutation == "evidence_level":
        payload["evidence_level"] = "list"
    elif mutation == "content_origin":
        payload["content_origin"] = "list"
    elif mutation == "cache_age_seconds":
        payload["cache_age_seconds"] = 1.0
    elif mutation == "captured_at":
        payload["captured_at"] = "2026-10-23T08:31:00Z"
    elif mutation == "observed_at":
        payload["observed_at"] = "2026-10-21T08:31:00Z"
    else:
        payload["source_context"] = {"discovered_company": "Tampered Company"}
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises((ArchiveCorruptionError, ArchiveSchemaError)):
        archive.read_page(snapshot_id)
    with pytest.raises((ArchiveCorruptionError, ArchiveSchemaError)):
        archive.verify()


def test_path_traversal_missing_references_and_invalid_observation_are_rejected(tmp_path):
    archive, _cid = new_archive(tmp_path)
    with pytest.raises(ValueError, match="safe"):
        archive.read_page("../manifest")
    with pytest.raises(ArchiveNotFoundError):
        archive.read_page("missing")
    with pytest.raises(ArchiveNotFoundError, match="candidate"):
        archive.write_page("not-recorded", page(), observation())
    with pytest.raises(ValueError):
        archive.write_page(
            candidate_id("fixture", source_job_id="1"),
            page(),
            {**observation(), "headers": {"Authorization": "secret"}},
        )


def test_reading_a_missing_archive_does_not_create_it(tmp_path):
    missing = tmp_path / "not-created"
    archive = SourceArchive(missing)
    with pytest.raises(ArchiveNotFoundError):
        archive.read_manifest()
    assert not missing.exists()


def test_manifest_is_relative_and_cannot_reference_missing_records(tmp_path):
    archive, cid = new_archive(tmp_path)
    snapshot_id = archive.write_page(cid, page(), observation())
    good = ArchiveManifest(
        run_id="run-1",
        conditions={"keyword": "AI intern", "location": "Remote"},
        candidate_ids=[cid],
        snapshot_ids=[snapshot_id],
        attempts_path="attempts.jsonl",
        created_at=NOW,
        code_revision="deadbeef",
        dirty_manifest_sha256=hashlib.sha256(b"dirty").hexdigest(),
    )
    assert archive.write_manifest(good) == "manifest.json"
    stored = archive.read_manifest()
    assert stored.candidate_ids == [cid]
    assert stored.snapshot_ids == [snapshot_id]
    assert stored.attempts_count == 0
    assert stored.attempts_sha256 == hashlib.sha256(b"").hexdigest()

    with pytest.raises(ArchiveNotFoundError, match="snapshot"):
        archive.write_manifest(good.model_copy(update={"snapshot_ids": ["invented"]}))


def test_concurrent_appends_keep_every_record_and_unique_sequence(tmp_path):
    archive, cid = new_archive(tmp_path)
    other_instance = SourceArchive(tmp_path / "run-archive")

    def write(index):
        (archive if index % 2 else other_instance).write_failure(
            cid,
            {
                "strategy": "http",
                "started_at": NOW,
                "elapsed_ms": index,
                "category": "transient",
            },
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(40)))
    rows = [
        json.loads(line)
        for line in (tmp_path / "run-archive" / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 40
    assert sorted(row["sequence"] for row in rows) == list(range(1, 41))


def test_os_lock_is_reentrant_and_serializes_spawned_processes(tmp_path):
    archive, cid = new_archive(tmp_path)
    with archive._locked():
        with archive._locked():
            archive.write_failure(
                cid,
                {"strategy": "http", "started_at": NOW, "elapsed_ms": 1, "category": "transient"},
            )

    context = multiprocessing.get_context("spawn")
    workers = [
        context.Process(target=_increment_counter_in_child, args=(tmp_path / "run-archive", 4)) for _ in range(4)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    assert (tmp_path / "run-archive" / "counter.txt").read_text(encoding="ascii") == "16"


def test_spawned_processes_allocate_contiguous_attempt_sequences(tmp_path):
    _archive, cid = new_archive(tmp_path)
    context = multiprocessing.get_context("spawn")
    workers = [
        context.Process(target=_append_failures_in_child, args=(tmp_path / "run-archive", cid, 5)) for _ in range(4)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    rows = [
        json.loads(line)
        for line in (tmp_path / "run-archive" / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["sequence"] for row in rows] == list(range(1, 21))
    assert all(row["schema_version"] == 1 for row in rows)


def test_manifest_covers_disk_inventory_and_hashes_attempt_ledger(tmp_path):
    archive, cid = new_archive(tmp_path)
    snapshot_id = archive.write_page(cid, page(), observation())
    archive.write_failure(
        cid,
        {"strategy": "http", "started_at": NOW, "elapsed_ms": 1, "category": "transient"},
    )
    good = manifest_for(cid, [snapshot_id])
    archive.write_manifest(good)
    stored = archive.read_manifest()
    attempts_raw = (tmp_path / "run-archive" / "attempts.jsonl").read_bytes()
    candidates_raw = (tmp_path / "run-archive" / "candidates.jsonl").read_bytes()
    assert stored.attempts_count == 1
    assert stored.attempts_sha256 == hashlib.sha256(attempts_raw).hexdigest()
    assert stored.candidates_count == 1
    assert stored.candidates_sha256 == hashlib.sha256(candidates_raw).hexdigest()

    with pytest.raises(ArchiveConflictError, match="inventory"):
        archive.write_manifest(good.model_copy(update={"candidate_ids": [], "snapshot_ids": []}))
    with pytest.raises(ArchiveConflictError, match="inventory"):
        archive.write_manifest(good.model_copy(update={"attempts_count": 0}))
    with pytest.raises(ArchiveConflictError, match="inventory"):
        archive.write_manifest(good.model_copy(update={"candidates_count": 0}))


def test_candidate_ledger_tamper_is_detected_beyond_the_row_digest(tmp_path):
    archive = SourceArchive(tmp_path / "run-archive")
    archive.write_candidate(
        CandidateEvidence(
            candidate_id="fixture-1",
            source_spec="fixture",
            source_job_id="native-1",
            requested_url=URL,
            keyword="AI intern",
            location="Remote",
            discovered_at=NOW,
        )
    )
    snapshot_id = archive.write_page("fixture-1", page(), observation())
    archive.write_manifest(manifest_for("fixture-1", [snapshot_id]))

    candidates_path = tmp_path / "run-archive" / "candidates.jsonl"
    row = json.loads(candidates_path.read_text(encoding="utf-8"))
    row["source_job_id"] = "native-2"
    row["identity_sha256"] = candidate_identity_sha256(
        "fixture-1",
        source_spec="fixture",
        source_job_id="native-2",
        requested_url=URL,
        canonical_url="https://example.com/jobs/1?lang=en",
    )
    candidates_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ArchiveCorruptionError, match="candidate (ledger|record binding|history)"):
        archive.verify()


def test_manifest_verify_detects_new_unlisted_records_and_deleted_attempts(tmp_path):
    archive, cid = new_archive(tmp_path)
    snapshot_id = archive.write_page(cid, page(), observation())
    archive.write_failure(
        cid,
        {"strategy": "http", "started_at": NOW, "elapsed_ms": 1, "category": "transient"},
    )
    archive.write_manifest(manifest_for(cid, [snapshot_id]))
    extra_snapshot = archive.write_page(
        cid,
        page("Title: AI Agent Intern\nNew immutable body"),
        observation(observed_at=NOW - timedelta(days=1)),
    )
    with pytest.raises(ArchiveConflictError, match="inventory"):
        archive.verify()

    archive.write_manifest(manifest_for(cid, [snapshot_id, extra_snapshot]))
    attempts = tmp_path / "run-archive" / "attempts.jsonl"
    attempts.unlink()
    with pytest.raises(ArchiveCorruptionError, match="attempt"):
        archive.verify()


def test_manifest_revision_rejects_rewritten_candidate_prefix(tmp_path):
    archive, cid = new_archive(tmp_path)
    archive.write_manifest(manifest_for(cid, []))

    candidates_path = tmp_path / "run-archive" / "candidates.jsonl"
    row = json.loads(candidates_path.read_text(encoding="utf-8"))
    row["location"] = "Rewritten City"
    candidates_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    archive.write_candidate(
        CandidateEvidence(
            candidate_id=cid,
            source_spec="fixture",
            source_job_id="1",
            requested_url=URL,
            keyword="AI intern",
            location="New City",
            discovered_at=NOW,
        )
    )

    with pytest.raises((ArchiveCorruptionError, ArchiveConflictError), match="revision|prefix|ledger"):
        archive.write_manifest(manifest_for(cid, []))


def test_manifest_revision_rejects_rewritten_attempt_prefix(tmp_path):
    archive, cid = new_archive(tmp_path)
    snapshot_id = archive.write_page(cid, page(), observation())
    archive.write_failure(
        cid,
        {"strategy": "http", "started_at": NOW, "elapsed_ms": 1, "category": "transient"},
    )
    archive.write_manifest(manifest_for(cid, [snapshot_id]))

    attempts_path = tmp_path / "run-archive" / "attempts.jsonl"
    row = json.loads(attempts_path.read_text(encoding="utf-8"))
    row["started_at"] = "2026-10-23T08:35:00Z"
    attempts_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    archive.write_failure(
        cid,
        {"strategy": "http", "started_at": NOW, "elapsed_ms": 2, "category": "removed"},
    )

    with pytest.raises((ArchiveCorruptionError, ArchiveConflictError), match="revision|prefix|ledger"):
        archive.write_manifest(manifest_for(cid, [snapshot_id]))


def test_manifest_revision_ledger_preserves_earlier_immutable_fields(tmp_path):
    archive, cid = new_archive(tmp_path)
    archive.write_manifest(manifest_for(cid, []))
    archive.write_failure(
        cid,
        {"strategy": "http", "started_at": NOW, "elapsed_ms": 1, "category": "transient"},
    )
    archive.write_manifest(manifest_for(cid, []))

    revisions_path = tmp_path / "run-archive" / "manifest-revisions.jsonl"
    revisions = [json.loads(line) for line in revisions_path.read_text(encoding="utf-8").splitlines()]
    revisions[0]["conditions"] = {"keyword": "changed", "location": "Remote"}
    revisions_path.write_text(
        "".join(json.dumps(row) + "\n" for row in revisions),
        encoding="utf-8",
    )

    with pytest.raises((ArchiveCorruptionError, ArchiveConflictError), match="revision|manifest"):
        archive.verify()


def test_existing_history_is_not_overwritten(tmp_path):
    archive, cid = new_archive(tmp_path)
    snapshot_id = archive.write_page(cid, page(), observation())
    snapshot_path = tmp_path / "run-archive" / "snapshots" / f"{snapshot_id}.json"
    original = snapshot_path.read_bytes()
    assert archive.write_page(cid, page(), observation()) == snapshot_id
    assert snapshot_path.read_bytes() == original


def test_corrupt_ledger_types_and_manifest_resume_conditions_are_rejected(tmp_path):
    archive, cid = new_archive(tmp_path)
    snapshot_id = archive.write_page(cid, page(), observation())
    manifest = ArchiveManifest(
        run_id="run-1",
        conditions={"keyword": "AI intern", "location": "Remote"},
        candidate_ids=[cid],
        snapshot_ids=[snapshot_id],
        attempts_path="attempts.jsonl",
        created_at=NOW,
        code_revision="deadbeef",
        dirty_manifest_sha256=hashlib.sha256(b"dirty").hexdigest(),
    )
    archive.write_manifest(manifest)
    with pytest.raises(ArchiveConflictError, match="manifest revision"):
        archive.write_manifest(manifest.model_copy(update={"conditions": {"keyword": "other", "location": "Remote"}}))
    with pytest.raises(ArchiveConflictError, match="manifest revision"):
        archive.write_manifest(manifest.model_copy(update={"code_revision": "changed"}))
    with pytest.raises(ArchiveConflictError, match="manifest revision"):
        archive.write_manifest(
            manifest.model_copy(update={"dirty_manifest_sha256": hashlib.sha256(b"different").hexdigest()})
        )

    candidates_path = tmp_path / "run-archive" / "candidates.jsonl"
    original = candidates_path.read_bytes()
    payload = json.loads(original.decode("utf-8").splitlines()[0])
    payload["query_context"] = {"page": True}
    candidates_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(ArchiveSchemaError):
        archive.read_candidates()


def test_attempts_are_contiguous_schema_versioned_and_verified(tmp_path):
    archive, cid = new_archive(tmp_path)
    snapshot_id = archive.write_page(cid, page(), observation())
    with pytest.raises(ArchiveConflictError, match="contiguous"):
        archive.write_failure(
            cid,
            {
                "sequence": 2,
                "strategy": "http",
                "started_at": NOW,
                "elapsed_ms": 1,
                "category": "transient",
            },
        )
    archive.write_failure(
        cid,
        {"strategy": "http", "started_at": NOW, "elapsed_ms": 1, "category": "transient"},
    )
    archive.write_failure(
        cid,
        {"strategy": "http", "started_at": NOW, "elapsed_ms": 2, "category": "removed"},
    )
    rows = [json.loads(line) for line in (tmp_path / "run-archive" / "attempts.jsonl").read_text().splitlines()]
    assert [row["schema_version"] for row in rows] == [1, 1]
    assert [row["sequence"] for row in rows] == [1, 2]
    archive.write_manifest(manifest_for(cid, [snapshot_id]))

    rows[0]["sequence"], rows[1]["sequence"] = 2, 1
    attempts_raw = "".join(json.dumps(row) + "\n" for row in rows).encode("utf-8")
    (tmp_path / "run-archive" / "attempts.jsonl").write_bytes(attempts_raw)
    manifest_path = tmp_path / "run-archive" / "manifest.json"
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_payload["attempts_sha256"] = hashlib.sha256(attempts_raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")
    with pytest.raises(ArchiveCorruptionError, match="revision|prefix|sequence"):
        archive.verify()


def test_attempt_unknown_schema_is_rejected(tmp_path):
    archive, cid = new_archive(tmp_path)
    archive.write_failure(
        cid,
        {"strategy": "http", "started_at": NOW, "elapsed_ms": 1, "category": "transient"},
    )
    path = tmp_path / "run-archive" / "attempts.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["schema_version"] = 99
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ArchiveSchemaError, match="schema_version"):
        archive.read_attempts()


def test_success_attempts_autoallocate_sequences_under_archive_transaction(tmp_path):
    archive, cid = new_archive(tmp_path)
    snapshot_id = archive.write_page(cid, page(), observation())
    archive.append_attempt(
        {
            "candidate_id": cid,
            "strategy": "official-api",
            "started_at": NOW,
            "elapsed_ms": 5,
            "status": "success",
            "snapshot_id": snapshot_id,
        }
    )
    archive.write_attempt(
        {
            "candidate_id": cid,
            "strategy": "http",
            "status": "success",
            "snapshot_id": snapshot_id,
        }
    )
    rows = archive.read_attempts()
    assert [row.sequence for row in rows] == [1, 2]
    assert all(row.status == "success" for row in rows)
    assert all(row.error_category is None for row in rows)


def test_spawned_success_attempt_allocations_are_contiguous(tmp_path):
    archive, cid = new_archive(tmp_path)
    snapshot_id = archive.write_page(cid, page(), observation())
    context = multiprocessing.get_context("spawn")
    workers = [
        context.Process(
            target=_append_success_in_child,
            args=(tmp_path / "run-archive", cid, snapshot_id, 3),
        )
        for _ in range(3)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    rows = archive.read_attempts()
    assert [row.sequence for row in rows] == list(range(1, 10))
    assert all(row.status == "success" for row in rows)


@pytest.mark.parametrize("already_published", [False, True])
def test_interrupted_manifest_publication_recovers_without_rewriting_history(tmp_path, monkeypatch, already_published):
    archive, cid = new_archive(tmp_path)
    manifest = manifest_for(cid, [])
    if already_published:
        archive.write_manifest(manifest)
    archive.write_failure(cid, {"strategy": "http", "category": "transient"})
    original_replace = archive._atomic_replace

    def fail_publication(path, content):
        if path == archive.manifest_path:
            raise OSError("interrupted publication")
        original_replace(path, content)

    monkeypatch.setattr(archive, "_atomic_replace", fail_publication)
    with pytest.raises(OSError, match="interrupted"):
        archive.write_manifest(manifest)
    history = archive.revisions_path.read_bytes()
    all_bytes = {p.relative_to(archive.root): p.read_bytes() for p in archive.root.rglob("*") if p.is_file()}

    reopened = SourceArchive(archive.root)
    assert reopened.verify() == {"candidates": 1, "snapshots": 0, "attempts": 1}
    assert reopened.read_manifest().attempts_count == 1
    assert all_bytes == {p.relative_to(archive.root): p.read_bytes() for p in archive.root.rglob("*") if p.is_file()}
    reopened.write_manifest(manifest)
    assert reopened.revisions_path.read_bytes() == history
    assert reopened.manifest_path.is_file()
    assert reopened.verify()["attempts"] == 1


def test_unpublished_revision_rejects_rewritten_prefix(tmp_path, monkeypatch):
    archive, cid = new_archive(tmp_path)
    manifest = manifest_for(cid, [])
    archive.write_manifest(manifest)
    archive.write_failure(cid, {"strategy": "http", "category": "transient"})
    monkeypatch.setattr(archive, "_atomic_replace", lambda *_: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        archive.write_manifest(manifest)
    committed = archive.manifest_path.read_bytes()
    raw = archive.attempts_path.read_bytes().replace(b'"transient"', b'"removed"')
    archive.attempts_path.write_bytes(raw)
    reopened = SourceArchive(archive.root)
    with pytest.raises((ArchiveCorruptionError, ArchiveConflictError)):
        reopened.verify()
    with pytest.raises((ArchiveCorruptionError, ArchiveConflictError)):
        reopened.write_manifest(manifest)
    assert reopened.manifest_path.read_bytes() == committed


def test_failure_whitelist_retains_operation_identity(tmp_path):
    archive, cid = new_archive(tmp_path)
    archive.write_failure(cid, {"strategy": "http", "category": "transient",
                                "operation_id": "open-1", "operation_kind": "open"})
    attempt = archive.read_attempts()[0]
    assert attempt.operation_id == "open-1"
    assert attempt.operation_kind == "open"


def test_recovery_publishes_pending_tail_before_preparing_newer_revision(tmp_path, monkeypatch):
    archive, cid = new_archive(tmp_path)
    manifest = manifest_for(cid, [])
    archive.write_manifest(manifest)
    archive.write_failure(cid, {"strategy": "http", "category": "transient"})
    monkeypatch.setattr(archive, "_atomic_replace", lambda *_: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        archive.write_manifest(manifest)
    reopened = SourceArchive(archive.root)
    reopened.write_failure(cid, {"strategy": "http", "category": "removed"})
    original_replace = reopened._atomic_replace

    def interrupt_newer_publication(path, content):
        if json.loads(content).get("attempts_count") == 2:
            raise OSError("second interruption")
        original_replace(path, content)

    monkeypatch.setattr(reopened, "_atomic_replace", interrupt_newer_publication)
    with pytest.raises(OSError):
        reopened.write_manifest(manifest)
    assert json.loads(reopened.manifest_path.read_bytes())["attempts_count"] == 1
    assert SourceArchive(archive.root).verify()["attempts"] == 2
