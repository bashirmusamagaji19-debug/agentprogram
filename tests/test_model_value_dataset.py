"""Contract tests for the frozen C3 controlled dataset."""

from __future__ import annotations

import hashlib
import json
import socket
from collections import Counter
from pathlib import Path

import pytest

from web_task_agent.model_value_dataset import (
    EDGE_CATEGORIES,
    HumanReviewRequiredError,
    build_model_value_dataset,
    load_case_pages,
    load_model_value_dataset,
)
from web_task_agent.model_value_evaluation import DatasetValidationError, validate_dataset
from web_task_agent.source_archive import SourceArchive


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def built_dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("model-value-build") / "model-value-v3"
    return build_model_value_dataset(root)


def test_build_is_exact_group_disjoint_and_validates_with_existing_schema(built_dataset: Path) -> None:
    tasks, catalog, manifest = load_model_value_dataset(built_dataset)

    assert len(tasks) == 60
    assert len({task.group_id for task in tasks}) == 30
    assert Counter(task.split for task in tasks) == {"dev": 20, "test": 40}
    assert Counter(task.group_id for task in tasks) == Counter({f"c3-g{index:02d}": 2 for index in range(1, 31)})
    assert len(set(catalog)) == 60
    assert set(catalog) == {task.case_id for task in tasks}

    by_group: dict[str, list[str]] = {}
    for task in tasks:
        by_group.setdefault(task.group_id, []).append(task.split)
    assert all(len(set(splits)) == 1 for splits in by_group.values())
    assert sum(1 for splits in by_group.values() if splits[0] == "dev") == 10
    assert sum(1 for splits in by_group.values() if splits[0] == "test") == 20

    validated = validate_dataset(tasks, manifest)
    assert validated["case_count"] == 60
    assert validated["group_count"] == 30
    assert validated["split_counts"] == {"dev": 20, "test": 40}

    dev_tasks, dev_catalog, _ = load_model_value_dataset(built_dataset, split="dev")
    assert len(dev_tasks) == 20
    assert len(dev_catalog) == 20
    assert {task.case_id for task in dev_tasks} == set(dev_catalog)
    assert all(task.split == "dev" for task in dev_tasks)
    with pytest.raises(ValueError):
        load_model_value_dataset(built_dataset, split="formal")  # type: ignore[arg-type]


def test_catalog_has_complete_fixed_inputs_and_meaningful_group_variation(built_dataset: Path) -> None:
    tasks, catalog, _ = load_model_value_dataset(built_dataset)
    required_case_keys = {
        "candidate_urls",
        "candidate_summaries",
        "job_key_by_url",
        "faults",
        "max_steps",
        "snapshot_refs",
        "snapshot_by_url",
        "scenario",
        "conditions",
        "group_id",
        "profile_id",
        "profile",
        "expected_outcome",
        "template_id",
        "edge_categories",
        "case_kind",
    }
    for task in tasks:
        case = catalog[task.case_id]
        assert required_case_keys <= set(case)
        assert case["group_id"] == task.group_id
        assert case["profile_id"] == task.profile_id
        assert case["expected_outcome"] == task.expected_outcome
        assert case["candidate_urls"]
        assert set(case["candidate_summaries"]) == set(case["candidate_urls"])
        assert set(case["job_key_by_url"]) == set(case["candidate_urls"])
        assert set(case["faults"]) == set(case["candidate_urls"])
        assert set(case["snapshot_by_url"]) == set(case["candidate_urls"])
        assert case["snapshot_refs"] == sorted(case["snapshot_by_url"].values())
        assert isinstance(case["max_steps"], int) and case["max_steps"] > 0
        assert case["profile"]["skills"]
        for summary in case["candidate_summaries"].values():
            assert set(summary) >= {"title", "company", "location", "source_spec"}
        for fault_list in case["faults"].values():
            assert isinstance(fault_list, list)
            assert all(fault in {"timeout", "removed", "error"} for fault in fault_list)

    templates = {case["template_id"] for case in catalog.values()}
    assert len(templates) == 30
    titles = {case["candidate_summaries"][case["candidate_urls"][0]]["title"] for case in catalog.values()}
    assert len(titles) >= 28
    for group_id in {task.group_id for task in tasks}:
        variants = [catalog[task.case_id] for task in tasks if task.group_id == group_id]
        assert len({variant["template_id"] for variant in variants}) == 1
        assert len({json.dumps(variant["conditions"], sort_keys=True) for variant in variants}) == 2


def test_edge_coverage_and_clean_failure_partial_refusal_mix(built_dataset: Path) -> None:
    _tasks, catalog, manifest = load_model_value_dataset(built_dataset)
    observed = {category for case in catalog.values() for category in case["edge_categories"]}
    assert set(EDGE_CATEGORIES) <= observed
    assert len(EDGE_CATEGORIES) >= 14
    assert {"clear_single_candidate", "multi_candidate_relevance", "cross_source_duplicate"} <= observed

    assert {case["expected_outcome"] for case in catalog.values()} >= {
        "target_reached",
        "partial",
        "refusal",
        "no_candidates",
    }
    assert {case["case_kind"] for case in catalog.values()} >= {"clean", "failure", "partial", "refusal"}
    assert any(any(faults for faults in case["faults"].values()) for case in catalog.values())
    assert any(not snapshots for snapshots in (case.get("successful_snapshot_urls", []) for case in catalog.values()))
    assert manifest["synthetic_data"] is True
    assert manifest["human_review_status"] == "pending"
    assert manifest["network_access_during_build"] is False
    assert manifest["model_calls_during_build"] == 0


def test_v3_formal_split_covers_boundary_behaviors_without_cross_split_leakage(
    built_dataset: Path,
) -> None:
    tasks, catalog, manifest = load_model_value_dataset(built_dataset)
    dev_tasks, dev_catalog, _ = load_model_value_dataset(built_dataset, split="dev")
    test_tasks, test_catalog, _ = load_model_value_dataset(built_dataset, split="test")

    assert len(dev_tasks) == 20
    assert len(test_tasks) == 40
    assert {case["scenario"] for case in dev_catalog.values()} >= {
        "clear_single_candidate",
        "cross_source_duplicate",
        "multi_candidate_relevance",
        "budget_priority",
        "timeout_then_url",
    }
    test_scenarios = {case["scenario"] for case in test_catalog.values()}
    assert set(EDGE_CATEGORIES) <= test_scenarios
    assert {
        "multi_candidate_relevance",
        "cross_source_duplicate",
        "budget_priority",
        "timeout_then_url",
        "all_candidates_failed",
        "insufficient_candidates",
        "multi_job_notice",
        "location_conflict",
        "unknown_location",
        "missing_text",
        "non_tech_job",
        "empty_model_response",
        "illegal_action",
    } <= test_scenarios
    assert manifest["split_counts"] == {"dev": 20, "test": 40}

    dev_urls = {url for case in dev_catalog.values() for url in case["candidate_urls"]}
    test_urls = {url for case in test_catalog.values() for url in case["candidate_urls"]}
    dev_keys = {key for case in dev_catalog.values() for key in case["job_key_by_url"].values()}
    test_keys = {key for case in test_catalog.values() for key in case["job_key_by_url"].values()}
    dev_bodies = {
        hashlib.sha256(page.content.encode("utf-8")).hexdigest()
        for case in dev_catalog.values()
        for page in load_case_pages(built_dataset, case).values()
    }
    test_bodies = {
        hashlib.sha256(page.content.encode("utf-8")).hexdigest()
        for case in test_catalog.values()
        for page in load_case_pages(built_dataset, case).values()
    }
    assert not (dev_urls & test_urls)
    assert not (dev_keys & test_keys)
    assert not (dev_bodies & test_bodies)
    assert not ({task.group_id for task in dev_tasks} & {task.group_id for task in test_tasks})
    assert len(tasks) == len(dev_tasks) + len(test_tasks)
    assert len(catalog) == len(dev_catalog) + len(test_catalog)


def test_v3_source_hashes_are_explicit_build_records_not_a_reproducibility_claim(
    built_dataset: Path,
) -> None:
    tasks, _catalog, manifest = load_model_value_dataset(built_dataset)
    result = validate_dataset(tasks, manifest)
    reproducibility = manifest["source_code_reproducibility"]

    assert manifest["source_code_hash_semantics"].startswith("historical_build_record")
    assert reproducibility["semantics"] == "historical_build_record_not_reproducibility_proof"
    assert reproducibility["current_match"] is True
    assert result["valid"] is True
    assert result["dataset_valid"] is True
    assert result["code_version_match"] is True
    assert result["source_hash_semantics"] == reproducibility["semantics"]


def test_v2_frozen_artifact_remains_available_for_audit() -> None:
    root = Path(__file__).resolve().parents[1] / "evaluations" / "model-value-v2"
    tasks, catalog, manifest = load_model_value_dataset(root)

    assert manifest["dataset_id"] == "model-value-v2"
    assert manifest["dataset_version"] == "2.0.0"
    assert manifest["manifest_self_sha256"] == (
        "d737c239d3f69e6ca82ba2e564b66c99e75ddd4ffe7cf736edf68c4e401bcd13"
    )
    assert len(tasks) == 60
    assert len(catalog) == 60


def test_repository_v3_artifact_is_complete_and_retains_human_review_state() -> None:
    root = Path(__file__).resolve().parents[1] / "evaluations" / "model-value-v3"
    tasks, catalog, manifest = load_model_value_dataset(root)
    result = validate_dataset(tasks, manifest)

    assert manifest["dataset_id"] == "model-value-v3"
    assert manifest["dataset_version"] == "3.0.0"
    assert result["dataset_valid"] is True
    assert result["split_counts"] == {"dev": 20, "test": 40}
    assert len(tasks) == 60
    assert len(catalog) == 60
    assert manifest["human_review_status"] == "pending"
    receipt_path = root / "HUMAN_REVIEW_RECEIPT.json"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert receipt["changed_labels"] == ["c3-g03-v1", "c3-g06-v1"]
        with pytest.raises(HumanReviewRequiredError, match="changed labels"):
            load_model_value_dataset(root, require_human_review=True)
    assert (root / "REVIEW.md").read_text(encoding="utf-8").startswith(
        "# model-value-v3 concrete human review packet"
    )


def test_v3_conditions_and_accepted_labels_have_literal_query_and_ai_role_evidence(
    built_dataset: Path,
) -> None:
    tasks, catalog, manifest = load_model_value_dataset(built_dataset)
    assert manifest["dataset_id"] == "model-value-v3"
    assert manifest["dataset_version"] == "3.0.0"
    assert manifest["revision_log"]["supersedes_dataset_id"] == "model-value-v2"
    assert manifest["revision_log"]["supersedes_manifest_self_sha256"] == (
        "d737c239d3f69e6ca82ba2e564b66c99e75ddd4ffe7cf736edf68c4e401bcd13"
    )

    ai_markers = (
        "AI",
        "Agent",
        "RAG",
        "模型",
        "算法",
        "智能",
        "机器学习",
        "NLP",
        "语音",
        "视觉",
        "推荐",
        "对话",
        "检索",
        "机器人学习",
        "编译器",
        "边缘推理",
        "时序预测",
        "标注",
        "多智能体",
    )
    assert len(tasks) == 60
    for task in tasks:
        case = catalog[task.case_id]
        audit = case["semantic_audit"]
        assert audit["query_terms"] == list(task.conditions["query_terms"])
        pages = load_case_pages(built_dataset, case)
        primary = pages[case["candidate_urls"][0]]
        for term in audit["query_terms"]:
            quote = audit["query_evidence"][term]
            assert term in quote and quote in primary.content
        role_quote = audit["ai_role_evidence"]
        assert role_quote in primary.content
        assert role_quote.startswith("Title:") or role_quote.startswith("- ")
        assert any(marker.casefold() in role_quote.casefold() for marker in ai_markers)
        assert not role_quote.startswith(("Company:", "Source note:"))
        for url, page_label in next(
            row for row in map(json.loads, (built_dataset / "labels.jsonl").read_text("utf-8").splitlines())
            if row["case_id"] == task.case_id
        )["page_labels"].items():
            if page_label["decision"] == "accepted":
                assert all(term in pages[url].content for term in audit["query_terms"])

    assert catalog["c3-g01-v1"]["semantic_audit"]["query_terms"][0] == "AI 数据工程"
    assert catalog["c3-g04-v1"]["expected_outcome"] == "refusal"
    assert catalog["c3-g06-v1"]["expected_outcome"] == "no_candidates"
    assert catalog["c3-g08-v1"]["expected_outcome"] == "partial"
    assert catalog["c3-g12-v1"]["expected_outcome"] == "target_reached"
    assert catalog["c3-g13-v1"]["expected_outcome"] == "target_reached"
    assert catalog["c3-g13-v1"]["reader_fault"] == "empty_model_response"
    assert catalog["c3-g14-v1"]["expected_outcome"] == "target_reached"
    assert catalog["c3-g14-v1"]["reader_fault"] is None
    assert len({case["conditions"]["keyword"] for case in catalog.values()}) == 30


def test_labels_are_page_grounded_but_explicitly_pending_human_review(built_dataset: Path) -> None:
    tasks, catalog, _ = load_model_value_dataset(built_dataset)
    labels = {}
    for line in (built_dataset / "labels.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        labels[record["case_id"]] = record
    assert set(labels) == {task.case_id for task in tasks}

    decisions: set[str] = set()
    for case_id, label in labels.items():
        assert label["group_id"] == catalog[case_id]["group_id"]
        assert label["split"] in {"dev", "test"}
        assert label["human_reviewed"] is False
        assert label["review_status"] == "pending_human_review"
        assert label["assistant_annotation"]["status"] == "pending_human_review"
        assert label["assistant_annotation"]["label_origin"] == "synthetic_assistant_proposal"
        assert set(label["page_labels"]) == set(catalog[case_id]["candidate_urls"])
        for url, page_label in label["page_labels"].items():
            assert page_label["decision"] in {"accepted", "review", "rejected"}
            decisions.add(page_label["decision"])
            assert page_label["job_key"] == catalog[case_id]["job_key_by_url"][url]
            assert isinstance(page_label["evidence_quotes"], dict)
        if label["expected_outcome"] == "target_reached":
            assert label["expected_job_keys"]
    assert decisions == {"accepted", "review", "rejected"}

    protocol = (built_dataset / "PROTOCOL.md").read_text(encoding="utf-8")
    assert "pre-registered" in protocol.casefold()
    assert "default decision" in protocol.casefold()
    assert "group" in protocol.casefold()
    assert "pending human review" in protocol.casefold()
    review = (built_dataset / "REVIEW.md").read_text(encoding="utf-8")
    assert review.startswith("# model-value-v3 concrete human review packet")
    assert review.count("Case `c3-g") >= 12
    assert "raw source facts" in review.casefold()
    assert "proposed label" in review.casefold()


def test_frozen_manifest_has_exact_file_and_source_hashes(built_dataset: Path) -> None:
    _tasks, _catalog, manifest = load_model_value_dataset(built_dataset)
    file_entries = manifest["files"]
    paths = [entry["path"] for entry in file_entries]
    assert len(paths) == len(set(paths))
    assert {
        "tasks.jsonl",
        "catalog.json",
        "labels.jsonl",
        "splits.json",
        "PROTOCOL.md",
        "REVIEW.md",
        manifest["prompt_file"],
    } <= set(paths)
    for entry in file_entries:
        assert not Path(entry["path"]).is_absolute()
        assert ".." not in Path(entry["path"]).parts
        assert sha256_file(built_dataset / entry["path"]) == entry["sha256"]

    assert manifest["catalog_sha256"] == sha256_file(built_dataset / "catalog.json")
    assert manifest["labels_sha256"] == sha256_file(built_dataset / "labels.jsonl")
    assert manifest["splits_sha256"] == sha256_file(built_dataset / "splits.json")
    assert manifest["prompts_sha256"] == sha256_file(built_dataset / manifest["prompt_file"])
    assert set(manifest["source_code_hashes"]) >= {
        "src/web_task_agent/model_value_dataset.py",
        "src/web_task_agent/model_value_evaluation.py",
        "src/web_task_agent/model_value_runner.py",
        "src/web_task_agent/reading_router.py",
        "src/web_task_agent/planner_routing.py",
        "src/web_task_agent/source_archive.py",
        "src/web_task_agent/source_provenance.py",
    }
    assert all(len(value) == 64 for value in manifest["source_code_hashes"].values())

    snapshot_entries = manifest["snapshots"]
    assert snapshot_entries
    for entry in snapshot_entries:
        assert sha256_file(built_dataset / entry["path"]) == entry["sha256"]
    assert {entry["path"] for entry in snapshot_entries} == {
        ref for case in _catalog.values() for ref in case["snapshot_refs"]
    }


def test_source_archive_round_trips_and_reader_pages_are_fixed(built_dataset: Path) -> None:
    tasks, catalog, manifest = load_model_value_dataset(built_dataset)
    archive_root = built_dataset / manifest["archive_path"]
    archive = SourceArchive(archive_root)
    archive.verify()

    task = next(task for task in tasks if task.case_id == "c3-g02-v1")
    pages = load_case_pages(built_dataset, catalog[task.case_id])
    assert set(pages) == set(catalog[task.case_id]["candidate_urls"])
    for url, page in pages.items():
        snapshot_id = Path(catalog[task.case_id]["snapshot_by_url"][url]).stem
        assert page.content == archive.read_page(snapshot_id).content
        assert page.url == url


def test_formal_loader_requires_an_explicit_external_human_review_receipt(built_dataset: Path) -> None:
    with pytest.raises(HumanReviewRequiredError, match="human review"):
        load_model_value_dataset(built_dataset, require_human_review=True)

    tasks, _catalog, manifest = load_model_value_dataset(built_dataset)
    reviewed_ids = [
        "c3-g02-v1",
        "c3-g03-v1",
        "c3-g05-v1",
        "c3-g06-v1",
        "c3-g09-v1",
        "c3-g01-v1",
        "c3-g25-v1",
        "c3-g15-v1",
        "c3-g16-v1",
        "c3-g17-v1",
        "c3-g18-v1",
        "c3-g19-v1",
    ]
    receipt = {
        "schema_version": 1,
        "dataset_id": manifest["dataset_id"],
        "manifest_sha256": manifest["manifest_self_sha256"],
        "reviewed_case_ids": reviewed_ids,
        "reviewer": "example-human-reviewer",
        "reviewed_at": "2026-10-23T09:00:00Z",
        "review_source": "REVIEW.md copied to the external review output",
        "proposed_label_version_sha256": manifest["labels_sha256"],
        "changed_labels": [],
        "status": "complete",
        "attestation": "I read the raw source facts and checked the listed proposed task and page labels.",
    }
    incomplete = dict(receipt, reviewer="", reviewed_case_ids=reviewed_ids[:11])
    (built_dataset / "HUMAN_REVIEW_RECEIPT.json").write_text(
        json.dumps(incomplete, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(HumanReviewRequiredError, match="human review"):
        load_model_value_dataset(built_dataset, require_human_review=True)

    unstratified = dict(receipt, reviewed_case_ids=[task.case_id for task in tasks[:12]])
    (built_dataset / "HUMAN_REVIEW_RECEIPT.json").write_text(
        json.dumps(unstratified, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(HumanReviewRequiredError, match="human review"):
        load_model_value_dataset(built_dataset, require_human_review=True)

    changed = dict(receipt, changed_labels=["c3-g12-v1"])
    (built_dataset / "HUMAN_REVIEW_RECEIPT.json").write_text(
        json.dumps(changed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(HumanReviewRequiredError, match="new dataset version"):
        load_model_value_dataset(built_dataset, require_human_review=True)

    stale_labels = dict(receipt, proposed_label_version_sha256="0" * 64)
    (built_dataset / "HUMAN_REVIEW_RECEIPT.json").write_text(
        json.dumps(stale_labels, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(HumanReviewRequiredError, match="human review"):
        load_model_value_dataset(built_dataset, require_human_review=True)

    (built_dataset / "HUMAN_REVIEW_RECEIPT.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    reviewed_tasks, _, _ = load_model_value_dataset(built_dataset, require_human_review=True)
    assert len(reviewed_tasks) == 60
    _, _, reviewed_manifest = load_model_value_dataset(built_dataset)
    coverage = reviewed_manifest["human_review_coverage"]
    assert coverage["status"] == "partial"
    assert coverage["reviewed_case_count"] == 12
    assert coverage["coverage_rate"] == pytest.approx(0.2)
    assert coverage["split_counts"] == {"dev": 5, "test": 7}
    assert coverage["normal_edge_counts"] == {"normal": 3, "edge": 9}
    authorship = reviewed_manifest["assistant_authorship"]
    assert authorship["labels_authored_by_assistant"] is True
    assert authorship["human_reviewed_case_count"] == 12
    assert authorship["unreviewed_case_count"] == 48


def test_loader_rejects_tampered_content_even_when_json_still_parses(built_dataset: Path) -> None:
    labels_path = built_dataset / "labels.jsonl"
    original = labels_path.read_text(encoding="utf-8")
    labels_path.write_text(original + "\n", encoding="utf-8")
    try:
        with pytest.raises(DatasetValidationError, match="hash"):
            load_model_value_dataset(built_dataset)
    finally:
        labels_path.write_text(original, encoding="utf-8")

    _tasks, catalog, manifest = load_model_value_dataset(built_dataset)
    case = catalog["c3-g01-v1"]
    snapshot_ref = case["snapshot_refs"][0]
    snapshot_path = built_dataset / snapshot_ref
    snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    body_path = built_dataset / manifest["archive_path"] / snapshot_payload["body_relative_path"]
    original_body = body_path.read_bytes()
    body_path.write_bytes(original_body + b"tamper")
    try:
        with pytest.raises(DatasetValidationError, match="snapshot|body|hash"):
            load_model_value_dataset(built_dataset)
    finally:
        body_path.write_bytes(original_body)


def test_builder_rejects_every_existing_root_and_makes_no_network_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = tmp_path / "already-there"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(FileExistsError, match="refus"):
        build_model_value_dataset(existing)
    assert marker.read_text(encoding="utf-8") == "do not overwrite"

    def fail_connect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the controlled dataset builder must not use the network")

    monkeypatch.setattr(socket.socket, "connect", fail_connect)
    built = build_model_value_dataset(tmp_path / "offline-dataset")
    assert (built / "manifest.json").is_file()


def test_dataset_contains_no_common_secret_or_private_material(built_dataset: Path) -> None:
    forbidden = (
        "authorization:",
        "set-cookie:",
        "api_key=",
        "password=",
        "BEGIN PRIVATE KEY",
    )
    for path in built_dataset.rglob("*"):
        if not path.is_file() or path.suffix not in {".json", ".jsonl", ".md", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert all(marker not in text for marker in forbidden), path


def test_repository_frozen_dataset_is_complete_and_still_pending_human_review() -> None:
    root = Path(__file__).resolve().parents[1] / "evaluations" / "model-value-v1"
    tasks, catalog, manifest = load_model_value_dataset(root)
    assert len(tasks) == 60
    assert len(catalog) == 60
    assert manifest["human_review_status"] == "pending"
    assert manifest["label_status"] == "unreviewed_assistant_proposal"
    assert manifest["human_review_coverage"]["status"] == "none"
    assert manifest["human_review_coverage"]["reviewed_case_count"] == 0
    assert manifest["assistant_authorship"]["unreviewed_case_count"] == 60
    advisory = manifest["dataset_advisories"][0]
    assert advisory["code"] == "v1_known_semantic_label_issues"
    assert advisory["replacement_dataset_id"] == "model-value-v3"
    assert not (root / "HUMAN_REVIEW_RECEIPT.json").exists()
    with pytest.raises(HumanReviewRequiredError, match="retired for formal inference"):
        load_model_value_dataset(root, require_human_review=True)
