import hashlib
import json
import socket
from datetime import UTC, datetime, timedelta
from urllib import request as urllib_request

import pytest

from web_task_agent.browser import PageHttpError
from web_task_agent.models import BrowserPage
from web_task_agent.page_reliability import PageRemovedError
from web_task_agent.source_archive import ArchiveCorruptionError, SourceArchive
from web_task_agent.source_provenance import ArchiveManifest, CandidateEvidence, candidate_id
from web_task_agent.source_replay import (
    ArchiveBrowser,
    ConditionsChangedError,
    MissingModelRecordError,
    MissingSnapshotError,
    RecordedEvidenceReader,
    prompt_sha256,
    reader_input_sha256,
)

NOW = datetime(2026, 10, 23, 8, 30, tzinfo=UTC)
URL = "https://example.com/jobs/1"


@pytest.fixture
def archived_replay(tmp_path):
    archive = SourceArchive(tmp_path / "archive")
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
            query_context={"job_id": "1", "page": 1},
        )
    )
    original = BrowserPage(
        url=URL,
        title="AI Agent Intern",
        content="Title: AI Agent Intern\nCompany: Example\nLocation: Remote\nRequirements: Python LangGraph",
        source="http",
    )
    snapshot_id = archive.write_page(
        cid,
        original,
        {
            "captured_at": NOW,
            "observed_at": NOW - timedelta(days=1),
            "content_origin": "http",
            "canonical_url": "https://example.com/jobs/1?lang=en",
            "evidence_level": "detail",
            "availability": "observed",
        },
    )
    archive.write_failure(
        cid,
        {"strategy": "http", "started_at": NOW, "elapsed_ms": 7, "category": "transient", "http_status": 504},
    )
    archive.write_failure(
        cid,
        {"strategy": "http", "started_at": NOW + timedelta(seconds=1), "elapsed_ms": 9, "category": "removed"},
    )
    manifest = ArchiveManifest(
        run_id="run-replay",
        conditions={"keyword": "AI intern", "location": "Remote"},
        candidate_ids=[cid],
        snapshot_ids=[snapshot_id],
        attempts_path="attempts.jsonl",
        created_at=NOW,
        code_revision="deadbeef",
        dirty_manifest_sha256=hashlib.sha256(b"dirty").hexdigest(),
    )
    archive.write_manifest(manifest)
    return tmp_path / "archive", snapshot_id, original


def block_network(*_args, **_kwargs):
    raise AssertionError("replay attempted network access")


def grouped_archive(tmp_path, rows):
    archive = SourceArchive(tmp_path / "grouped")
    cid = candidate_id("fixture", source_job_id="1")
    archive.write_candidate(CandidateEvidence(candidate_id=cid, source_spec="fixture", source_job_id="1",
        requested_url=URL, keyword="AI intern", location="Remote", discovered_at=NOW))
    snapshots = []
    for body in ("first exact body", "later exact body"):
        snapshots.append(archive.write_page(cid, BrowserPage(url=URL, title="AI intern", content=body, source="http"),
            {"captured_at": NOW, "observed_at": NOW, "content_origin": "http", "evidence_level": "detail"}))
    for row in rows:
        values = {"candidate_id": cid, "strategy": "http", "started_at": NOW, **row}
        if "snapshot_index" in values:
            values["snapshot_id"] = snapshots[values.pop("snapshot_index")]
        archive.append_attempt(values)
    archive.write_manifest(ArchiveManifest(run_id="grouped", conditions={"keyword": "AI intern", "location": "Remote"},
        candidate_ids=[cid], snapshot_ids=snapshots, attempts_path="attempts.jsonl", created_at=NOW,
        code_revision="rev", dirty_manifest_sha256="a" * 64))
    return archive


@pytest.mark.asyncio
async def test_grouped_internal_failures_return_that_operations_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", block_network)
    archive = grouped_archive(tmp_path, [
        {"operation_id": "open-1", "operation_kind": "open", "status": "error",
         "error_category": "transient", "http_status": 503},
        {"operation_id": "open-1", "operation_kind": "open", "snapshot_index": 0},
        {"operation_id": "open-2", "operation_kind": "open", "snapshot_index": 1},
    ])
    browser = ArchiveBrowser(archive.root, mode="attempts")
    assert (await browser.open_url(URL)).content == "first exact body"
    assert (await browser.open_url(URL)).content == "later exact body"


@pytest.mark.asyncio
async def test_grouped_final_failure_then_retry_skips_search_operations(tmp_path):
    archive = grouped_archive(tmp_path, [
        {"operation_id": "search-1", "operation_kind": "search", "status": "error", "error_category": "removed"},
        {"operation_id": "open-1", "operation_kind": "open", "status": "error",
         "error_category": "transient", "http_status": 503},
        {"operation_id": "open-1", "operation_kind": "open", "status": "error",
         "error_category": "blocked", "http_status": 429},
        {"operation_id": "open-2", "operation_kind": "open", "snapshot_index": 0},
    ])
    browser = ArchiveBrowser(archive.root, mode="attempts")
    with pytest.raises(PageHttpError) as failed:
        await browser.open_url(URL)
    assert failed.value.status_code == 429
    assert (await browser.open_url(URL)).content == "first exact body"


@pytest.mark.asyncio
async def test_search_skips_raw_discovery_and_returns_unopened_placeholders(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", block_network)
    archive = SourceArchive(tmp_path / "discovery")
    search_url = "https://example.com/search?q=AI"
    search_id = candidate_id("search", requested_url=search_url)
    archive.write_candidate(CandidateEvidence(candidate_id=search_id, source_spec="search", requested_url=search_url,
        keyword="AI intern", location="Remote", discovered_at=NOW, query_context={"type": "search_page"}))
    snapshot = archive.write_page(search_id,
        BrowserPage(url=search_url, title="Search", content="raw search body", source="http"),
        {"captured_at": NOW, "observed_at": NOW, "content_origin": "http", "evidence_level": "list"})
    cid = candidate_id("browser-search", requested_url=URL)
    archive.write_candidate(CandidateEvidence(candidate_id=cid, source_spec="browser-search", requested_url=URL,
        keyword="AI intern", location="Remote", discovered_at=NOW,
        query_context={"type": "discovered_url", "source": snapshot}))
    archive.write_manifest(ArchiveManifest(run_id="discovery", conditions={}, candidate_ids=[search_id, cid],
        snapshot_ids=[snapshot], attempts_path="attempts.jsonl", created_at=NOW,
        code_revision="rev", dirty_manifest_sha256="a" * 64))
    browser = ArchiveBrowser(archive.root)
    found = await browser.search("AI intern", 2)
    assert len(found) == 1
    assert found[0].url == URL
    assert found[0].content == ""
    assert found[0].metadata["candidate_urls"] == [URL]
    assert found[0].metadata["query_context"]["source"] == snapshot
    assert "snapshot_id" not in found[0].metadata
    with pytest.raises(MissingSnapshotError):
        await browser.open_url(URL)


@pytest.mark.asyncio
async def test_search_page_at_job_url_is_not_job_content(tmp_path):
    archive = SourceArchive(tmp_path / "same-url-discovery")
    search_id = candidate_id("browser-search-page", requested_url=URL)
    cid = candidate_id("browser-search", requested_url=URL)
    for identity, source, kind in (
        (search_id, "browser-search-page", "search_page"),
        (cid, "browser-search", "discovered_url"),
    ):
        archive.write_candidate(CandidateEvidence(candidate_id=identity, source_spec=source,
            requested_url=URL, keyword="AI intern", location="Remote", discovered_at=NOW,
            query_context={"type": kind}))
    snapshot = archive.write_page(search_id,
        BrowserPage(url=URL, title="Search", content="raw discovery", source="http"),
        {"captured_at": NOW, "observed_at": NOW, "content_origin": "http", "evidence_level": "list"})
    archive.write_manifest(ArchiveManifest(run_id="same-url", conditions={}, candidate_ids=[search_id, cid],
        snapshot_ids=[snapshot], attempts_path="attempts.jsonl", created_at=NOW,
        code_revision="rev", dirty_manifest_sha256="a" * 64))
    with pytest.raises(MissingSnapshotError):
        await ArchiveBrowser(archive.root).open_url(URL)


@pytest.mark.asyncio
async def test_content_replay_reads_only_archive_and_exact_metadata(archived_replay, monkeypatch):
    archive_root, snapshot_id, original = archived_replay
    before = {path.relative_to(archive_root): path.read_bytes() for path in archive_root.rglob("*") if path.is_file()}
    monkeypatch.setattr(socket.socket, "connect", block_network)
    monkeypatch.setattr(socket, "create_connection", block_network)
    monkeypatch.setattr(socket, "getaddrinfo", block_network)
    monkeypatch.setattr(urllib_request, "urlopen", block_network)

    browser = ArchiveBrowser(archive_root, mode="content")
    loaded = await browser.open_url(URL)
    assert loaded.content == original.content
    assert loaded.title == original.title
    assert loaded.metadata["snapshot_id"] == snapshot_id
    assert loaded.metadata["candidate_id"] == candidate_id("fixture", source_job_id="1")
    assert loaded.metadata["observed_at"] == "2026-10-22T08:30:00Z"

    found = await browser.search("AI intern Remote", 1)
    assert [item.metadata["snapshot_id"] for item in found] == [snapshot_id]
    after = {path.relative_to(archive_root): path.read_bytes() for path in archive_root.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.asyncio
async def test_attempts_replay_failures_in_order_then_returns_content(archived_replay, monkeypatch):
    archive_root, _snapshot_id, original = archived_replay
    monkeypatch.setattr(socket.socket, "connect", block_network)
    browser = ArchiveBrowser(archive_root, mode="attempts")

    with pytest.raises(PageHttpError) as transient:
        await browser.open_url(URL)
    assert transient.value.status_code == 504
    with pytest.raises(PageRemovedError):
        await browser.open_url(URL)
    loaded = await browser.open_url(URL)
    assert loaded.content == original.content


@pytest.mark.asyncio
async def test_unknown_url_and_unknown_query_fail_explicitly(archived_replay):
    browser = ArchiveBrowser(archived_replay[0])
    with pytest.raises(MissingSnapshotError, match="missing_snapshot"):
        await browser.open_url("https://example.com/jobs/unknown")
    assert await browser.search("unrecorded conditions", 3) == []


@pytest.mark.asyncio
async def test_corrupt_archive_is_rejected_before_replay(archived_replay):
    archive_root, snapshot_id, _original = archived_replay
    payload_path = archive_root / "snapshots" / f"{snapshot_id}.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    (archive_root / payload["body_relative_path"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(ArchiveCorruptionError):
        await ArchiveBrowser(archive_root).open_url(URL)


@pytest.mark.asyncio
async def test_replay_rejects_snapshot_metadata_tampering(archived_replay):
    archive_root, snapshot_id, _original = archived_replay
    payload_path = archive_root / "snapshots" / f"{snapshot_id}.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["canonical_url"] = "https://example.com/jobs/1?lang=zh"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ArchiveCorruptionError):
        await ArchiveBrowser(archive_root).open_url(URL)


@pytest.mark.asyncio
async def test_changed_conditions_require_explicit_replay_policy(archived_replay):
    browser = ArchiveBrowser(
        archived_replay[0],
        conditions={"keyword": "AI intern", "location": "Beijing"},
    )

    with pytest.raises(ConditionsChangedError, match="conditions"):
        await browser.open_url(URL)
    with pytest.raises(ConditionsChangedError, match="conditions"):
        await browser.search("AI intern Beijing", 1)


@pytest.mark.asyncio
async def test_changed_conditions_can_explicitly_use_archived_results(archived_replay):
    browser = ArchiveBrowser(
        archived_replay[0],
        conditions={"keyword": "AI intern", "location": "Beijing"},
        conditions_mismatch="use_archived",
    )

    loaded = await browser.open_url(URL)
    found = await browser.search("AI intern Beijing", 1)

    assert loaded.metadata["conditions_match"] is False
    assert loaded.metadata["requested_conditions"]["location"] == "Beijing"
    assert found[0].metadata["conditions_match"] is False


@pytest.mark.asyncio
async def test_matching_conditions_replay_normally(archived_replay):
    browser = ArchiveBrowser(
        archived_replay[0],
        conditions={"keyword": "AI intern", "location": "Remote"},
    )

    loaded = await browser.open_url(URL)

    assert loaded.metadata["conditions_match"] is True
    assert loaded.metadata["requested_conditions"]["location"] == "Remote"


def test_recorded_reader_requires_exact_identity_and_never_live_calls(archived_replay):
    _archive_root, _snapshot_id, original = archived_replay
    prompt = "read only the archived page"
    result = {"status": "ok", "reading": {"page_type": "single_job", "jobs": [], "detail_links": [], "notes": []}}
    records = [
        {
            "input_sha256": reader_input_sha256(original),
            "prompt_sha256": prompt_sha256(prompt),
            "provider": "fixture",
            "model": "fixture-model",
            "result": result,
        }
    ]
    reader = RecordedEvidenceReader(records, provider="fixture", model="fixture-model", prompt=prompt)
    assert reader.read(original) == result

    wrong_provider = RecordedEvidenceReader(records, provider="other", model="fixture-model", prompt=prompt)
    with pytest.raises(MissingModelRecordError, match="missing_model_record"):
        wrong_provider.read(original)
    changed = original.model_copy(update={"content": original.content + " changed"})
    with pytest.raises(MissingModelRecordError, match="missing_model_record"):
        reader.read(changed)


def test_recorded_reader_rejects_wrong_prompt(archived_replay):
    _archive_root, _snapshot_id, original = archived_replay
    records = [
        {
            "input_sha256": reader_input_sha256(original),
            "prompt_sha256": prompt_sha256("prompt A"),
            "provider": "fixture",
            "model": "m",
            "result": {"status": "ok"},
        }
    ]
    reader = RecordedEvidenceReader(records, provider="fixture", model="m", prompt="prompt B")
    with pytest.raises(MissingModelRecordError):
        reader.read(original)


def test_recorded_reader_requires_matching_parameters(archived_replay):
    _archive_root, _snapshot_id, original = archived_replay
    prompt = "read the archived page"
    records = [
        {
            "input_sha256": reader_input_sha256(original),
            "prompt_sha256": prompt_sha256(prompt),
            "provider": "fixture",
            "model": "fixture-model",
            "parameters": {"temperature": 0},
            "result": {"status": "zero"},
        }
    ]
    reader = RecordedEvidenceReader(
        records,
        provider="fixture",
        model="fixture-model",
        prompt=prompt,
        parameters={"temperature": 0},
    )
    assert reader.read(original) == {"status": "zero"}

    changed_parameters = RecordedEvidenceReader(
        records,
        provider="fixture",
        model="fixture-model",
        prompt=prompt,
        parameters={"temperature": 1},
    )
    with pytest.raises(MissingModelRecordError):
        changed_parameters.read(original)


def test_recorded_reader_legacy_records_only_match_default_parameters(archived_replay):
    _archive_root, _snapshot_id, original = archived_replay
    prompt = "read the archived page"
    records = [
        {
            "input_sha256": reader_input_sha256(original),
            "prompt_sha256": prompt_sha256(prompt),
            "provider": "fixture",
            "model": "fixture-model",
            "result": {"status": "legacy-default"},
        }
    ]

    default_reader = RecordedEvidenceReader(
        records,
        provider="fixture",
        model="fixture-model",
        prompt=prompt,
    )
    explicit_default = RecordedEvidenceReader(
        records,
        provider="fixture",
        model="fixture-model",
        prompt=prompt,
        parameters={},
    )
    nondefault = RecordedEvidenceReader(
        records,
        provider="fixture",
        model="fixture-model",
        prompt=prompt,
        parameters={"temperature": 0},
    )

    assert default_reader.read(original) == {"status": "legacy-default"}
    assert explicit_default.read(original) == {"status": "legacy-default"}
    with pytest.raises(MissingModelRecordError):
        nondefault.read(original)


def test_recorded_reader_keeps_distinct_parameter_results(archived_replay):
    _archive_root, _snapshot_id, original = archived_replay
    prompt = "read the archived page"
    identity = {
        "input_sha256": reader_input_sha256(original),
        "prompt_sha256": prompt_sha256(prompt),
        "provider": "fixture",
        "model": "fixture-model",
    }
    records = [
        {**identity, "parameters": {"temperature": 0}, "result": {"status": "zero"}},
        {**identity, "parameters": {"temperature": 1}, "result": {"status": "one"}},
    ]

    zero = RecordedEvidenceReader(
        records,
        provider="fixture",
        model="fixture-model",
        prompt=prompt,
        parameters={"temperature": 0},
    )
    one = RecordedEvidenceReader(
        records,
        provider="fixture",
        model="fixture-model",
        prompt=prompt,
        parameters={"temperature": 1},
    )

    assert zero.read(original) == {"status": "zero"}
    assert one.read(original) == {"status": "one"}
