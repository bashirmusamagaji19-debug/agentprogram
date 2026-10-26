import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from web_task_agent.models import BrowserPage
from web_task_agent.reading_replay import run_reading_replay
from web_task_agent.source_archive import ArchiveCorruptionError, ArchiveSchemaError, SourceArchive
from web_task_agent.source_provenance import ArchiveManifest, CandidateEvidence


def write_input(root, *, two_cities=False):
    root.mkdir()
    page = BrowserPage(
        url="https://example.org/jobs/1", title="大模型算法实习生", content=(
            "公司：示例科技\n工作地点：上海\n岗位职责：开发大模型推理系统。\n"
            "任职要求：熟悉 Python，实习三个月。\n"
        ),
    )
    cases = []
    for index, city in enumerate((["上海", "北京"] if two_cities else ["上海"]), 1):
        path = root / f"case-{index}" / "search_fetch"
        path.mkdir(parents=True)
        (path / "pages.json").write_text(json.dumps([page.model_dump()]), encoding="utf-8")
        cases.append({"case": {"keyword": "大模型 实习", "location": city}})
    (root / "comparison.json").write_text(json.dumps({"status": "completed", "cases": cases}), encoding="utf-8")
    return page


def reading():
    return {"status": "ok", "usage": {"total_tokens": 15}, "elapsed_seconds": 0.1, "reading": {
        "page_type": "single_job", "jobs": [{
            "title": {"value": "大模型算法实习生", "quote": "大模型算法实习生"},
            "company": {"value": "示例科技", "quote": "公司：示例科技"},
            "location": {"value": "上海", "quote": "工作地点：上海"},
            "responsibilities": ["开发大模型推理系统。"],
            "requirements": ["熟悉 Python，实习三个月。"],
            "is_ai_role": True, "role_evidence": ["开发大模型推理系统。"],
            "is_internship": True, "internship_evidence": ["大模型算法实习生"],
        }],
    }}


@pytest.mark.asyncio
async def test_replay_reuses_identical_model_input_but_evaluates_each_city(tmp_path):
    from web_task_agent.reading_replay import run_reading_replay

    input_dir = tmp_path / "input"
    page = write_input(input_dir, two_cities=True)
    original = (input_dir / "case-1/search_fetch/pages.json").read_bytes()
    calls = []

    class Reader:
        def read(self, value):
            calls.append(value.model_dump())
            return reading()

    result = await run_reading_replay(input_dir=input_dir, output_dir=tmp_path / "output", reader=Reader(),
                                      provider="fixture", model="fixture", concurrency=2)
    assert len(calls) == result["model_calls"] == 1
    assert calls[0]["content"] == page.content
    assert result["cases"][0]["llm_accepted"] == 1
    assert result["cases"][1]["llm_accepted"] == 0
    assert result["rows"][0]["body_sha256"] == hashlib.sha256(page.content.encode()).hexdigest()
    assert result["total_tokens"] == 15
    assert (input_dir / "case-1/search_fetch/pages.json").read_bytes() == original
    assert (tmp_path / "output/replay.md").is_file()


@pytest.mark.asyncio
async def test_model_errors_stay_in_denominator_and_do_not_fall_back_to_rule(tmp_path):
    from web_task_agent.reading_replay import run_reading_replay

    input_dir = tmp_path / "input"
    write_input(input_dir)

    class Reader:
        def read(self, value):
            return {"status": "error", "error_type": "TimeoutError", "usage": {}, "elapsed_seconds": 1}

    result = await run_reading_replay(input_dir=input_dir, output_dir=tmp_path / "output", reader=Reader(),
                                      provider="fixture", model="fixture")
    assert result["cases"][0]["pages"] == 1
    assert result["cases"][0]["llm_errors"] == 1
    assert result["cases"][0]["llm_accepted"] == 0
    assert result["rows"][0]["llm"]["status"] == "error"
    assert result["status"] == "completed"


class FixtureReader:
    def __init__(self, result=None):
        self.calls = []
        self.result = reading() if result is None else result

    def read(self, page):
        self.calls.append(page.model_dump(mode="json"))
        return self.result


def write_archive(root, *, history=False, duplicate_source=False):
    now = datetime(2026, 10, 23, 8, 30, tzinfo=UTC)
    page = BrowserPage(
        url="https://example.org/jobs/1", title="大模型算法实习生",
        content=("公司：示例科技\r\n工作地点：上海\r\n岗位职责：开发大模型推理系统。\r\n"
                 "任职要求：熟悉 Python，实习三个月。\r\n"),
        source="http", metadata={"discovered_company": "示例科技", "tier": "fixture"},
    )
    archive = SourceArchive(root)
    cid = archive.write_candidate(CandidateEvidence(
        candidate_id="fixture-1", source_spec="fixture", source_job_id="1",
        requested_url=page.url, keyword="大模型 实习", location="上海", discovered_at=now,
        company="示例科技",
    ))
    ids = []
    if history:
        ids.append(archive.write_page(cid, page.model_copy(update={"content": "Old body"}), {
            "captured_at": now - timedelta(days=1), "observed_at": now - timedelta(days=3),
            "content_origin": "http", "canonical_url": page.url,
            "evidence_level": "detail", "availability": "observed",
        }))
    sid = archive.write_page(cid, page, {
        "captured_at": now, "observed_at": now - timedelta(days=2),
        "content_origin": "cache", "cache_age_seconds": 172800.0,
        "canonical_url": page.url, "evidence_level": "historical", "availability": "unknown",
    })
    ids.append(sid)
    candidate_ids = [cid]
    if duplicate_source:
        second_cid = archive.write_candidate(CandidateEvidence(
            candidate_id="fixture-2", source_spec="fixture-other", source_job_id="1",
            requested_url=page.url, keyword="大模型 实习", location="上海", discovered_at=now,
        ))
        candidate_ids.append(second_cid)
        ids.append(archive.write_page(second_cid, page, {
            "captured_at": now, "observed_at": now - timedelta(days=2),
            "content_origin": "http", "canonical_url": page.url,
            "evidence_level": "detail", "availability": "observed",
        }))
    archive.write_manifest(ArchiveManifest(
        run_id=root.name, conditions={"keyword": "大模型 实习", "location": "上海"},
        candidate_ids=candidate_ids, snapshot_ids=ids, attempts_path="attempts.jsonl", created_at=now,
        code_revision="fixture", dirty_manifest_sha256=hashlib.sha256(b"fixture").hexdigest(),
    ))
    return archive, sid, page


@pytest.mark.asyncio
@pytest.mark.parametrize("input_form", ["root", "manifest", "container", "archive_container"])
async def test_source_archive_formats_preserve_verified_bytes_context_and_time(tmp_path, monkeypatch, input_form):
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("source replay must not fetch")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    container = tmp_path / "input"
    archive, sid, page = write_archive(container / "source-archive" / "run-1")
    source_files = {p.relative_to(archive.root): p.read_bytes() for p in archive.root.rglob("*") if p.is_file()}
    inputs = {"root": archive.root, "manifest": archive.manifest_path,
              "container": container, "archive_container": archive.root.parent}
    reader = FixtureReader()
    result = await run_reading_replay(
        input_dir=inputs[input_form], output_dir=tmp_path / "output", reader=reader,
        provider="fixture", model="fixture",
    )
    assert result["mode"] == "reader_experiment"
    assert result["source_format"] == "source_archive"
    assert result["provenance_status"] == "verified_source_archive"
    assert result["manifest_ref"].endswith("manifest.json")
    assert result["manifest_sha256"] == hashlib.sha256(archive.manifest_path.read_bytes()).hexdigest()
    assert result["model_calls"] == len(reader.calls) == 1
    assert reader.calls[0]["content"] == page.content
    assert reader.calls[0]["metadata"]["snapshot_id"] == sid
    assert reader.calls[0]["metadata"]["observed_at"] == "2026-10-21T08:30:00Z"
    assert reader.calls[0]["metadata"]["tier"] == "fixture"
    row = result["rows"][0]
    assert row["body_sha256"] == hashlib.sha256(page.content.encode("utf-8")).hexdigest()
    assert row["provenance"]["snapshot_id"] == sid
    assert row["provenance"]["observed_at"] == "2026-10-21T08:30:00Z"
    assert row["provenance"]["archive_input_sha256"] != row["input_sha256"]
    snapshots = json.loads((tmp_path / "output/snapshots.json").read_text(encoding="utf-8"))
    frozen = snapshots[row["input_sha256"]]
    assert frozen["content"].encode("utf-8") == page.content.encode("utf-8")
    assert frozen["provenance"][0]["snapshot_id"] == sid
    assert frozen["provenance"][0]["manifest_ref"] == result["manifest_ref"]
    assert "reader_experiment" in (tmp_path / "output/replay.md").read_text(encoding="utf-8")
    assert source_files == {p.relative_to(archive.root): p.read_bytes() for p in archive.root.rglob("*") if p.is_file()}


@pytest.mark.asyncio
async def test_legacy_inputs_mark_partial_provenance_and_strip_private_metadata(tmp_path):
    root = tmp_path / "input"
    write_input(root)
    path = root / "case-1/search_fetch/pages.json"
    items = json.loads(path.read_text(encoding="utf-8"))
    items[0]["metadata"] = {"observed_at": "2026-10-01T00:00:00Z", "content_origin": "http",
                            "api_key": "synthetic-private-value", "run_context": {"resume": "private"}}
    path.write_text(json.dumps(items), encoding="utf-8")
    reader = FixtureReader()
    result = await run_reading_replay(input_dir=root, output_dir=tmp_path / "output", reader=reader,
                                      provider="fixture", model="fixture")
    assert result["provenance_status"] == "legacy_partial_provenance"
    assert result["manifest_ref"] is None
    assert result["rows"][0]["provenance"]["observed_at"] == "2026-10-01T00:00:00Z"
    assert result["rows"][0]["provenance"]["provenance_status"] == "legacy_partial_provenance"
    assert "api_key" not in reader.calls[0]["metadata"]
    for output_file in (tmp_path / "output").rglob("*.json"):
        assert "synthetic-private-value" not in output_file.read_text(encoding="utf-8")
        assert "run_context" not in output_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_changed_archive_conditions_require_explicit_reevaluation(tmp_path):
    archive, _, _ = write_archive(tmp_path / "run-1")
    reader = FixtureReader()
    with pytest.raises(ValueError, match="conditions"):
        await run_reading_replay(
            input_dir=archive.root, output_dir=tmp_path / "output", reader=reader,
            provider="fixture", model="fixture",
            conditions=[{"keyword": "大模型 实习", "location": "北京"}],
        )
    assert reader.calls == []
    assert not (tmp_path / "output").exists()


@pytest.mark.asyncio
async def test_archive_model_reuse_gates_cities_independently_and_preserves_conditions(tmp_path):
    archive, sid, _ = write_archive(tmp_path / "run-1")
    reader = FixtureReader()
    conditions = [{"keyword": "大模型 实习", "location": city} for city in ["上海", "北京"]]
    result = await run_reading_replay(
        input_dir=archive.root, output_dir=tmp_path / "output", reader=reader,
        provider="fixture", model="fixture", conditions=conditions, conditions_mismatch="reevaluate",
    )
    assert result["model_calls"] == len(reader.calls) == 1
    assert [case["pages"] for case in result["cases"]] == [1, 1]
    assert [row["llm"]["status"] for row in result["rows"]] == ["accepted", "rejected"]
    assert [row["provenance"]["conditions_match"] for row in result["rows"]] == [True, False]
    assert result["rows"][1]["provenance"]["archived_conditions"]["location"] == "上海"
    assert result["rows"][1]["provenance"]["requested_conditions"]["location"] == "北京"
    snapshots = json.loads((tmp_path / "output/snapshots.json").read_text(encoding="utf-8"))
    assert len(next(iter(snapshots.values()))["provenance"]) == 2
    assert all(row["provenance"]["snapshot_id"] == sid for row in result["rows"])


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,field", [
    ({"status": "ok", "reading": {"page_type": "insufficient", "jobs": []}}, "llm_review"),
    ({"status": "error", "error_type": "TimeoutError", "usage": {}}, "llm_errors"),
])
async def test_archive_review_and_errors_keep_every_city_in_denominator(tmp_path, payload, field):
    archive, _, _ = write_archive(tmp_path / "run-1")
    result = await run_reading_replay(
        input_dir=archive.root, output_dir=tmp_path / "output", reader=FixtureReader(payload),
        provider="fixture", model="fixture",
        conditions=[{"keyword": "大模型 实习", "location": city} for city in ["上海", "北京"]],
        conditions_mismatch="reevaluate",
    )
    assert len(result["rows"]) == 2
    assert all(case["pages"] == case[field] == 1 for case in result["cases"])
    assert all(case["llm_accepted"] == 0 for case in result["cases"])


@pytest.mark.asyncio
async def test_archive_content_uses_latest_snapshot_for_each_candidate(tmp_path):
    archive, sid, page = write_archive(tmp_path / "run-1", history=True)
    result = await run_reading_replay(input_dir=archive.root, output_dir=tmp_path / "output",
                                      reader=FixtureReader(), provider="fixture", model="fixture")
    assert len(result["rows"]) == 1
    assert result["rows"][0]["provenance"]["snapshot_id"] == sid
    assert result["rows"][0]["body_sha256"] == hashlib.sha256(page.content.encode()).hexdigest()


@pytest.mark.asyncio
async def test_ambiguous_archive_discovery_is_rejected_before_reader(tmp_path):
    for run in ["run-2", "run-1"]:
        write_archive(tmp_path / "input/source-archive" / run)
    reader = FixtureReader()
    with pytest.raises(ValueError, match="ambiguous"):
        await run_reading_replay(input_dir=tmp_path / "input", output_dir=tmp_path / "output",
                                  reader=reader, provider="fixture", model="fixture")
    assert reader.calls == []
    assert not (tmp_path / "output").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["body", "context"])
async def test_archive_integrity_is_checked_before_experiment(tmp_path, tamper):
    archive, sid, page = write_archive(tmp_path / "run-1")
    if tamper == "body":
        path = archive.root / "bodies" / f"{hashlib.sha256(page.content.encode()).hexdigest()}.txt"
        path.write_bytes(b"tampered")
    else:
        path = archive.root / "snapshots" / f"{sid}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["source_context"]["tier"] = "tampered"
        path.write_text(json.dumps(payload), encoding="utf-8")
    reader = FixtureReader()
    with pytest.raises((ArchiveCorruptionError, ArchiveSchemaError), match="hash|schema"):
        await run_reading_replay(input_dir=archive.root, output_dir=tmp_path / "output",
                                  reader=reader, provider="fixture", model="fixture")
    assert reader.calls == []
    assert not (tmp_path / "output").exists()


@pytest.mark.asyncio
async def test_identical_model_inputs_keep_all_distinct_source_references(tmp_path):
    archive, _, _ = write_archive(tmp_path / "run-1", duplicate_source=True)
    reader = FixtureReader()
    result = await run_reading_replay(input_dir=archive.root, output_dir=tmp_path / "output",
                                      reader=reader, provider="fixture", model="fixture")
    assert result["model_calls"] == len(reader.calls) == 1
    assert result["cases"][0]["pages"] == len(result["rows"]) == 2
    assert len({row["provenance"]["snapshot_id"] for row in result["rows"]}) == 2
    assert {row["provenance"]["source_spec"] for row in result["rows"]} == {"fixture", "fixture-other"}
    snapshots = json.loads((tmp_path / "output/snapshots.json").read_text(encoding="utf-8"))
    assert len(next(iter(snapshots.values()))["provenance"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, [], {"status": "error"}, {"status": "ok", "reading": {}}])
async def test_invalid_reader_results_remain_errors_in_denominator(tmp_path, payload):
    archive, _, _ = write_archive(tmp_path / "run-1")
    result = await run_reading_replay(input_dir=archive.root, output_dir=tmp_path / "output",
                                      reader=FixtureReader(payload), provider="fixture", model="fixture")
    assert result["status"] == "completed"
    assert result["cases"][0]["pages"] == result["cases"][0]["llm_errors"] == 1
    assert result["rows"][0]["llm"]["status"] == "error"


@pytest.mark.asyncio
async def test_private_reader_context_is_not_exported(tmp_path):
    archive, _, _ = write_archive(tmp_path / "run-1")
    payload = {**reading(), "api_key": "synthetic-private-value", "run_context": {"resume": "private"}}
    await run_reading_replay(input_dir=archive.root, output_dir=tmp_path / "output",
                             reader=FixtureReader(payload), provider="fixture", model="fixture")
    for path in (tmp_path / "output").rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "synthetic-private-value" not in text
        assert "run_context" not in text


@pytest.mark.asyncio
async def test_missing_legacy_observation_time_stays_unknown(tmp_path):
    root = tmp_path / "input"
    write_input(root)
    result = await run_reading_replay(input_dir=root, output_dir=tmp_path / "output",
                                      reader=FixtureReader(), provider="fixture", model="fixture")
    assert result["rows"][0]["provenance"]["observed_at"] is None


@pytest.mark.asyncio
async def test_real_legacy_comparison_case_settings_remain_compatible(tmp_path):
    root = tmp_path / "input"
    write_input(root)
    path = root / "comparison.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["cases"][0]["case"].update({
        "target_count": 5, "official_specs": ["tencent-campus", "xiaomi"],
        "skills": ["Python", "RAG", "Agent"],
    })
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = await run_reading_replay(input_dir=root, output_dir=tmp_path / "output",
                                      reader=FixtureReader(), provider="fixture", model="fixture")
    assert result["cases"][0]["llm_accepted"] == 1
    assert result["provenance_status"] == "legacy_partial_provenance"


@pytest.mark.asyncio
async def test_archive_ledger_corruption_cannot_be_bypassed_by_reading_pages(tmp_path):
    archive, _, _ = write_archive(tmp_path / "run-1")
    archive.write_failure("fixture-1", TimeoutError())
    reader = FixtureReader()
    with pytest.raises(ArchiveCorruptionError, match="ledger"):
        await run_reading_replay(input_dir=archive.root, output_dir=tmp_path / "output",
                                  reader=reader, provider="fixture", model="fixture")
    assert reader.calls == []
