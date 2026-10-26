"""Tests for the C5 paired model-value benchmark runner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.evidence_reader import GroundedJob, PageReading, QuotedField
from web_task_agent.model_value_dataset import load_model_value_dataset
from web_task_agent.model_value_evaluation import CasePrediction, ModelValueTask
from web_task_agent.model_value_runner import (
    ArchivePageResolver,
    BenchmarkRunConfig,
    RecordingLlmTransport,
    RunModelCallBudget,
    ScopedModelCallBudget,
    _budget_state_from_journal,
    _catalog_view,
    _reader_terminal_reason,
    _resolve_case,
    build_cli_parser,
    main,
    planned_episode_order,
    run_benchmark,
)
from web_task_agent.models import BrowserPage
from web_task_agent.planner_routing import ModelBudgetExceeded


def make_task(
    *,
    case_id: str = "case-a",
    group_id: str = "group-a",
    split: str = "dev",
    target: int = 1,
    allowed: tuple[str, ...] | None = ("job-a",),
) -> ModelValueTask:
    return ModelValueTask(
        case_id=case_id,
        group_id=group_id,
        split=split,
        profile_id="profile-a",
        conditions={"keyword": "AI Agent Intern", "location": "Remote"},
        target_count=target,
        allowed_job_keys=None if allowed is None else list(allowed),
        snapshot_refs=[f"{case_id}.json"],
    )


def make_catalog(
    task: ModelValueTask,
    *,
    urls: tuple[str, ...] = ("https://example.test/jobs/a",),
    keys: tuple[str, ...] = ("job-a",),
    refs: tuple[str, ...] | None = None,
    faults: dict[str, list[str]] | None = None,
) -> dict[str, dict]:
    snapshot_refs = refs if refs is not None else (f"{task.case_id}.json",)
    return {
        task.case_id: {
            "candidate_urls": list(urls),
            "candidate_summaries": {
                url: {
                    "title": "AI Agent Intern",
                    "company": "Acme AI",
                    "location": "Remote",
                    "source_spec": "fixture",
                }
                for url in urls
            },
            "job_key_by_url": dict(zip(urls, keys, strict=True)),
            "faults": faults or {},
            "max_steps": 30,
            "snapshot_refs": list(snapshot_refs),
            "snapshot_by_url": dict(zip(urls, snapshot_refs, strict=True)),
        }
    }


def make_page(url: str, *, suffix: str = "") -> BrowserPage:
    return BrowserPage(
        url=url,
        title=f"AI Agent Intern{suffix}",
        content=(
            f"AI Agent Intern{suffix}\nCompany: Acme AI\nLocation: Remote\n"
            "Requirements: Python and LangGraph\n"
            "Responsibilities: Build and evaluate AI agents"
        ),
        source="fixture",
    )


def loader_for(tasks: list[ModelValueTask], catalogs: dict[str, dict], manifest: dict | None = None):
    calls: list[dict] = []

    def load(root, *, split=None, require_human_review=False):
        calls.append(
            {"root": Path(root), "split": split, "require_human_review": require_human_review}
        )
        return list(tasks), dict(catalogs), manifest or {"dataset_version": "fixture-v1"}

    load.calls = calls
    return load


def read_results(path: Path) -> list[CasePrediction]:
    return [
        CasePrediction.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_archive_page_resolver_uses_the_nested_run_as_archive_root() -> None:
    dataset = Path(__file__).resolve().parents[1] / "evaluations" / "model-value-v1"
    _tasks, catalog, _manifest = load_model_value_dataset(dataset)
    case = catalog["c3-g01-v1"]
    ref = case["snapshot_refs"][0]

    page = ArchivePageResolver(dataset)(ref)

    assert page.url == case["candidate_urls"][0]
    assert page.content


def test_resolved_pages_follow_candidate_urls_not_sorted_snapshot_order() -> None:
    dataset = Path(__file__).resolve().parents[1] / "evaluations" / "model-value-v1"
    tasks, catalog, _manifest = load_model_value_dataset(dataset)
    task = next(item for item in tasks if item.case_id == "c3-g02-v1")
    catalog_case = catalog[task.case_id]

    resolved, _profile = _resolve_case(
        task,
        _catalog_view(task, catalog_case),
        ArchivePageResolver(dataset),
        "AI 实习",
        "杭州",
    )

    for url, page in resolved.pages_by_candidate.items():
        assert page.url == url


def test_catalog_rejects_missing_snapshot_url_mapping() -> None:
    task = make_task()
    catalog = make_catalog(task)
    del catalog[task.case_id]["snapshot_by_url"]

    with pytest.raises(ValueError, match="snapshot_by_url must be a mapping"):
        _catalog_view(task, catalog[task.case_id])


def test_catalog_rejects_incomplete_snapshot_url_mapping() -> None:
    task = make_task()
    catalog = make_catalog(task)
    catalog[task.case_id]["snapshot_by_url"] = {}

    with pytest.raises(ValueError, match="snapshot_by_url must exactly cover"):
        _catalog_view(task, catalog[task.case_id])


def test_cli_exposes_exact_c5_flags_and_rejects_formal_limits():
    parser = build_cli_parser()
    args = parser.parse_args(
        [
            "--suite", "reader", "--strategies", "rules,always", "--dataset", "data",
            "--split", "dev", "--provider", "qwen", "--model", "fixed-model",
            "--runs", "2", "--seed", "7", "--limit-cases", "6", "--max-calls", "12",
            "--max-planner-calls-per-case", "4", "--max-observed-tokens", "99",
            "--case-deadline-seconds", "30", "--output-dir", "out", "--resume",
            "--exploratory",
        ]
    )
    assert args.suite == "reader"
    assert args.strategies == ("rules", "always")
    assert args.max_planner_calls_per_case == 4
    assert args.case_deadline_seconds == 30
    assert args.resume is True
    assert args.exploratory is True

    with pytest.raises(ValidationError, match="limit-cases"):
        BenchmarkRunConfig(
            suite="planner",
            strategies=("rules",),
            dataset=Path("data"),
            split="test",
            limit_cases=6,
        )
    with pytest.raises(ValidationError, match="exploratory"):
        BenchmarkRunConfig(
            suite="planner",
            strategies=("rules",),
            dataset=Path("data"),
            split="test",
            exploratory=True,
            validate_only=True,
        )


def test_cli_validate_only_uses_the_frozen_dataset_loader(capsys):
    dataset = Path(__file__).resolve().parents[1] / "evaluations" / "model-value-v1"
    assert main([
        "--suite", "planner",
        "--dataset", str(dataset),
        "--split", "dev",
        "--validate-only",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "validate_only"
    assert result["validation"]["valid"] is True
    assert result["validation"]["dataset_valid"] is True
    assert result["validation"]["source_hash_semantics"] == (
        "historical_build_record_not_reproducibility_proof"
    )


@pytest.mark.asyncio
async def test_dry_run_is_provider_free_and_does_not_create_output(tmp_path):
    task = make_task()
    loader = loader_for([task], make_catalog(task))

    def forbid(**kwargs):
        raise AssertionError(f"provider construction is forbidden: {kwargs}")

    output = tmp_path / "dry-output"
    result = await run_benchmark(
        BenchmarkRunConfig(
            suite="planner",
            strategies=("rules", "always", "adaptive"),
            dataset=tmp_path,
            split="dev",
            runs=1,
            dry_run=True,
            output_dir=output,
            max_calls=60,
        ),
        dataset_loader=loader,
        planner_factory=forbid,
        reader_factory=forbid,
    )

    assert result["mode"] == "dry_run"
    assert result["estimated_max_requests"] == 8
    assert not output.exists()
    assert len(loader.calls) == 1
    assert loader.calls[0]["require_human_review"] is False


@pytest.mark.asyncio
async def test_validate_only_reads_dataset_without_output_or_provider(tmp_path):
    task = make_task(case_id="case-test", group_id="group-test", split="test")
    loader = loader_for(
        [task], make_catalog(task), {"formal_test": True, "human_reviewed": True}
    )
    dataset_manifest = tmp_path / "manifest.json"
    dataset_manifest.write_text('{"frozen":true}', encoding="utf-8")
    before = hashlib.sha256(dataset_manifest.read_bytes()).hexdigest()
    output = tmp_path / "never-created"

    result = await run_benchmark(
        BenchmarkRunConfig(
            suite="planner", strategies=("rules",), dataset=tmp_path,
            split="test", validate_only=True, output_dir=output,
        ),
        dataset_loader=loader,
        planner_factory=lambda **kwargs: (_ for _ in ()).throw(AssertionError("no provider")),
        reader_factory=lambda **kwargs: (_ for _ in ()).throw(AssertionError("no provider")),
    )

    assert result["mode"] == "validate_only"
    assert result["validation"]["valid"] is True
    assert not output.exists()
    assert loader.calls[0]["require_human_review"] is True
    assert hashlib.sha256(dataset_manifest.read_bytes()).hexdigest() == before


@pytest.mark.asyncio
async def test_reader_uses_db_side_effects_and_counts_only_real_calls(tmp_path):
    task = make_task()
    catalog = make_catalog(task)
    page = BrowserPage(
        url="https://example.test/jobs/a",
        title="AI Agent Role",
        content=(
            "AI Agent Role\nCompany: Acme AI\nLocation: Remote\n"
            "Requirements: Python and LangGraph\n"
            "Responsibilities: Build and evaluate AI agents\n"
            "Internship program available"
        ),
        source="fixture",
    )
    loader = loader_for([task], catalog)
    readers = []

    reading = PageReading(
        page_type="single_job",
        jobs=[
            GroundedJob(
                title=QuotedField(value="AI Agent Role", quote="AI Agent Role"),
                company=QuotedField(value="Acme AI", quote="Acme AI"),
                location=QuotedField(value="Remote", quote="Remote"),
                requirements=["Python and LangGraph"],
                responsibilities=["Build and evaluate AI agents"],
                is_ai_role=True,
                role_evidence=["Build and evaluate AI agents"],
                is_internship=True,
                internship_evidence=["Internship program available"],
            )
        ],
    )

    class FakeReader:
        def __init__(self):
            self.calls = 0
            self.telemetry = type("Telemetry", (), {"records": []})()

        def read(self, page):
            self.calls += 1
            return {
                "status": "ok",
                "reading": reading.model_dump(mode="json"),
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }

    def reader_factory(**kwargs):
        reader = FakeReader()
        readers.append(reader)
        return reader

    output = tmp_path / "reader-output"
    result = await run_benchmark(
        BenchmarkRunConfig(
            suite="reader", strategies=("always", "adaptive"), dataset=tmp_path,
            split="dev", runs=1, output_dir=output, max_calls=20,
            max_observed_tokens=100_000,
        ),
        dataset_loader=loader,
        reader_factory=reader_factory,
        page_resolver=lambda ref: page,
    )

    predictions = read_results(output / "results.jsonl")
    assert len(predictions) == 2
    assert {prediction.strategy for prediction in predictions} == {"always", "adaptive"}
    assert all(prediction.saved_job_keys == ("job-a",) for prediction in predictions)
    assert all(prediction.delivered_job_keys == ("job-a",) for prediction in predictions)
    assert all(prediction.total_tokens == 15 for prediction in predictions)
    assert all(prediction.planner_calls == 1 for prediction in predictions)
    assert len(readers) == 2
    assert all(reader.calls == 1 for reader in readers)
    assert result["summary"]["selected_strategies"] == ["always", "adaptive"]
    run_manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert set(run_manifest["code_version"]["observed_source_code_hashes_at_run"]) >= {
        "src/web_task_agent/model_value_runner.py",
        "src/web_task_agent/reading_router.py",
        "src/web_task_agent/planner_routing.py",
    }
    assert "declared_dataset_source_code_hashes" in run_manifest["code_version"]


@pytest.mark.asyncio
async def test_reader_suppresses_cross_source_duplicate_business_writes(tmp_path):
    urls = ("https://example.test/jobs/a", "https://example.test/jobs/mirror")
    task = make_task(allowed=("job-a",))
    catalog = make_catalog(task, urls=urls, keys=("job-a", "job-a"), refs=("a", "b"))
    pages = {
        "a": make_page(urls[0]),
        "b": make_page(urls[1]),
    }
    output = tmp_path / "reader-duplicate-output"

    result = await run_benchmark(
        BenchmarkRunConfig(
            suite="reader",
            strategies=("rules",),
            dataset=tmp_path,
            split="dev",
            runs=1,
            output_dir=output,
            max_calls=20,
            max_observed_tokens=100_000,
        ),
        dataset_loader=loader_for([task], catalog),
        page_resolver=pages.__getitem__,
    )

    prediction = read_results(output / "results.jsonl")[0]
    assert prediction.delivered_job_keys == ("job-a",)
    assert prediction.saved_job_keys == ("job-a",)
    assert prediction.duplicate_writes == 0
    detail = result["episode_details"][0]
    assert detail["db_job_count"] == 1
    assert detail["duplicate_delivery_suppressed"] == 1


def test_scoped_budget_caps_requests_and_unknown_usage_stays_none():
    shared = RunModelCallBudget(
        max_calls=2, max_observed_tokens=100, deadline_seconds=60
    )
    scoped = ScopedModelCallBudget(shared, max_calls=1, deadline_seconds=30)
    reservation = scoped.reserve(estimated_tokens=9)
    scoped.finish(reservation, usage=None)

    snapshot = shared.snapshot()
    assert snapshot["calls"] == 1
    assert snapshot["usage_missing"] == 1
    assert snapshot["observed_tokens"] is None
    assert snapshot["reserved_tokens"] == 0
    assert snapshot["known_tokens"] == 0
    assert snapshot["spent_token_estimate"] == 9
    with pytest.raises(ModelBudgetExceeded):
        scoped.reserve(estimated_tokens=1)

    other = ScopedModelCallBudget(shared, max_calls=1, deadline_seconds=30)
    second = other.reserve(estimated_tokens=1)
    other.finish(second, usage={"total_tokens": 2})
    assert shared.snapshot()["calls"] == 2
    assert shared.snapshot()["observed_tokens"] is None
    assert shared.snapshot()["usage_missing"] == 1
    assert shared.snapshot()["known_tokens"] == 2
    assert shared.snapshot()["reserved_tokens"] == 0
    assert shared.snapshot()["spent_token_estimate"] == 11
    with pytest.raises(ModelBudgetExceeded):
        RunModelCallBudget(
            max_calls=0, max_observed_tokens=100, deadline_seconds=30
        ).reserve(estimated_tokens=1)


@pytest.mark.asyncio
async def test_zero_request_cap_halts_before_model_factory_construction(tmp_path):
    task = make_task()
    loader = loader_for([task], make_catalog(task))

    def forbidden(**kwargs):
        raise AssertionError("a zero request budget must not construct a model")

    output = tmp_path / "budget-output"
    result = await run_benchmark(
        BenchmarkRunConfig(
            suite="reader", strategies=("always",), dataset=tmp_path, split="dev",
            runs=1, output_dir=output, max_calls=0,
        ),
        dataset_loader=loader,
        reader_factory=forbidden,
        page_resolver=lambda ref: make_page("https://example.test/jobs/a"),
    )

    assert result["halted_reason"] == "request_budget_exhausted"
    assert read_results(output / "results.jsonl") == []
    assert result["summary"]["missing_planned_episodes"]


@pytest.mark.asyncio
async def test_planner_controlled_faults_are_consumed_in_order(tmp_path):
    task = make_task(allowed=("job-b",))
    first = "https://example.test/jobs/first"
    second = "https://example.test/jobs/second"
    catalog = make_catalog(
        task,
        urls=(first, second),
        keys=("job-a", "job-b"),
        refs=("first.json", "second.json"),
        faults={first: ["timeout", "removed"], second: []},
    )
    pages = {
        "first.json": make_page(first, suffix=" First"),
        "second.json": make_page(second, suffix=" Second"),
    }
    output = tmp_path / "fault-output"

    result = await run_benchmark(
        BenchmarkRunConfig(
            suite="planner", strategies=("rules",), dataset=tmp_path, split="dev",
            runs=1, output_dir=output,
        ),
        dataset_loader=loader_for([task], catalog),
        page_resolver=pages.__getitem__,
    )

    predictions = read_results(output / "results.jsonl")
    assert len(predictions) == 1
    assert predictions[0].saved_job_keys == ("job-b",)
    assert [event["fault"] for event in result["episode_details"][0]["fault_events"]] == [
        "timeout", "removed"
    ]


@pytest.mark.asyncio
async def test_rules_maps_to_policy_and_always_constructs_actual_planner(tmp_path):
    task = make_task()
    catalog = make_catalog(task)
    adapter_calls = []

    class SpyPlanner:
        def __init__(self, budget):
            self.budget = budget
            self.telemetry = type("Telemetry", (), {"requests": []})()

        async def decide(self, state):
            reservation = self.budget.reserve(estimated_tokens=50)
            usage = {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
            try:
                return DeterministicAgentPolicy().decide(state)
            finally:
                self.budget.finish(reservation, usage=usage)
                self.telemetry.requests.append(
                    {
                        "model": "fixture-model", "provider": "qwen", "status": "ok",
                        "usage_complete": True, "prompt_tokens": 4,
                        "completion_tokens": 2, "total_tokens": 6,
                    }
                )

    def planner_factory(**kwargs):
        adapter_calls.append(kwargs)
        return SpyPlanner(kwargs["budget"])

    output = tmp_path / "planner-output"
    result = await run_benchmark(
        BenchmarkRunConfig(
            suite="planner", strategies=("rules", "always"), dataset=tmp_path,
            split="dev", provider="qwen", model="fixture-model", runs=1,
            output_dir=output, max_calls=20, max_observed_tokens=5000,
        ),
        dataset_loader=loader_for([task], catalog),
        planner_factory=planner_factory,
        page_resolver=lambda ref: make_page("https://example.test/jobs/a"),
    )

    assert len(adapter_calls) >= 1
    predictions = read_results(output / "results.jsonl")
    by_strategy = {prediction.strategy: prediction for prediction in predictions}
    assert by_strategy["rules"].planner_calls == 0
    assert by_strategy["always"].planner_calls >= 1
    assert by_strategy["rules"].saved_job_keys == ("job-a",)
    assert by_strategy["always"].saved_job_keys == ("job-a",)
    assert result["episode_details"]


def test_recording_transport_redacts_prompt_bodies_and_records_failures():
    seen = []

    def transport(url, headers, payload, timeout_seconds):
        seen.append((url, headers, payload, timeout_seconds))
        return {
            "model": "response-model",
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }

    recorder = RecordingLlmTransport(transport, provider="qwen", model="configured-model")
    payload = {
        "model": "configured-model", "temperature": 0, "max_tokens": 20,
        "messages": [{"role": "system", "content": "secret system body"},
                     {"role": "user", "content": "secret page body"}],
    }
    recorder("https://example.test/chat", {"Authorization": "Bearer hidden"}, payload, 3)
    record = recorder.records[-1]
    assert len(record["prompt_hash"]) == 64
    assert len(record["input_hash"]) == 64
    assert record["model"] == "configured-model"
    assert record["response_model"] == "response-model"
    assert record["params"]["temperature"] == 0
    serialized = json.dumps(record)
    assert "secret page body" not in serialized
    assert "Bearer hidden" not in serialized
    assert "secret system body" not in serialized

    def fail(*args):
        raise TimeoutError("do not persist this text")

    failing = RecordingLlmTransport(fail, provider="qwen", model="configured-model")
    with pytest.raises(TimeoutError):
        failing("https://example.test/chat", {}, payload, 3)
    assert failing.records[-1]["status"] == "error"
    assert failing.records[-1]["timeout"] is True
    assert "do not persist this text" not in json.dumps(failing.records)
    assert failing.failures == [failing.records[-1]]


@pytest.mark.asyncio
async def test_resume_appends_only_unfinished_keys_and_rejects_duplicates(tmp_path):
    tasks = [make_task(case_id="case-a"), make_task(case_id="case-b", group_id="group-b")]
    catalogs = {}
    pages = {}
    for task in tasks:
        catalogs.update(
            make_catalog(
                task,
                urls=(f"https://example.test/jobs/{task.case_id}",),
                keys=("job-a",) if task.case_id == "case-a" else ("job-b",),
            )
        )
        pages[f"{task.case_id}.json"] = make_page(
            f"https://example.test/jobs/{task.case_id}",
            suffix=f" {task.case_id}",
        )
    loader = loader_for(tasks, catalogs)
    output = tmp_path / "resume-output"
    config = BenchmarkRunConfig(
        suite="reader", strategies=("rules",), dataset=tmp_path, split="dev",
        runs=1, output_dir=output,
    )

    await run_benchmark(
        config, dataset_loader=loader, page_resolver=pages.__getitem__
    )
    results_path = output / "results.jsonl"
    lines = results_path.read_text(encoding="utf-8").splitlines()
    results_path.write_text(lines[0] + "\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="resume"):
        await run_benchmark(
            config, dataset_loader=loader, page_resolver=pages.__getitem__
        )

    with pytest.raises(ValueError, match="dataset manifest"):
        await run_benchmark(
            config.model_copy(update={"resume": True}),
            dataset_loader=loader_for(tasks, catalogs, {"dataset_version": "fixture-v2"}),
            page_resolver=pages.__getitem__,
        )

    resumed = await run_benchmark(
        config.model_copy(update={"resume": True}),
        dataset_loader=loader,
        page_resolver=pages.__getitem__,
    )
    assert len(read_results(results_path)) == 2
    assert resumed["resumed_keys"] == 1

    valid_lines = results_path.read_text(encoding="utf-8").splitlines()
    unknown = CasePrediction(
        case_id="case-z", strategy="rules", round=1,
        delivered_job_keys=[], saved_job_keys=[], terminal_status="failed",
        terminal_reason="no_candidates", planner_calls=0, consumed_steps=0,
        elapsed_ms=0, unauthorized_writes=0, duplicate_writes=0,
        constraint_violations=0, status="executed",
    )
    with results_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(unknown.model_dump(mode="json")) + "\n")
    with pytest.raises(ValueError, match="outside the frozen plan"):
        await run_benchmark(
            config.model_copy(update={"resume": True}),
            dataset_loader=loader,
            page_resolver=pages.__getitem__,
        )

    results_path.write_text("\n".join(valid_lines) + "\n", encoding="utf-8")
    with results_path.open("a", encoding="utf-8") as stream:
        stream.write(valid_lines[0] + "\n")
    with pytest.raises(ValueError, match="duplicate"):
        await run_benchmark(
            config.model_copy(update={"resume": True}),
            dataset_loader=loader,
            page_resolver=pages.__getitem__,
        )


def test_paired_order_is_grouped_and_deterministically_rotated():
    tasks = [make_task(case_id=f"case-{index}", group_id=f"group-{index}") for index in range(4)]
    order = planned_episode_order(
        tasks, ("rules", "always", "adaptive"), runs=3, seed=17, suite="planner"
    )
    assert len(order) == 4 * 3 * 3
    for case_index in range(4):
        for round_number in range(1, 4):
            block = order[(case_index * 3 + round_number - 1) * 3:(case_index * 3 + round_number) * 3]
            assert {item[1] for item in block} == {"rules", "always", "adaptive"}
            assert len({item[1] for item in block}) == 3
    assert order == planned_episode_order(
        tasks, ("rules", "always", "adaptive"), runs=3, seed=17, suite="planner"
    )
    rotations = {tuple(item[1] for item in order[index:index + 3]) for index in range(0, len(order), 3)}
    assert len(rotations) > 1
    assert order != planned_episode_order(
        tasks, ("rules", "always", "adaptive"), runs=3, seed=18, suite="planner"
    )


def test_budget_journal_releases_known_reservations_and_conservatively_counts_inflight(
    tmp_path: Path,
) -> None:
    journal = tmp_path / "budget-journal.jsonl"
    budget = RunModelCallBudget(
        max_calls=10,
        max_observed_tokens=10_000,
        deadline_seconds=30,
        journal_path=journal,
    )

    settled = budget.reserve(estimated_tokens=100)
    budget.finish(settled, usage={"total_tokens": 10})
    inflight = budget.reserve(estimated_tokens=200)

    live = budget.snapshot()
    assert live["reserved_tokens"] == 200
    assert live["known_tokens"] == 10
    assert live["spent_token_estimate"] == 210

    reconciled = _budget_state_from_journal(journal)
    assert reconciled == {
        "prior_calls": 2,
        "prior_spent_tokens": 210,
        "prior_known_tokens": 10,
        "prior_usage_missing": 1,
    }
    resumed = RunModelCallBudget(
        max_calls=10,
        max_observed_tokens=10_000,
        deadline_seconds=30,
        journal_path=journal,
        **reconciled,
    ).snapshot()
    assert resumed["calls"] == 2
    assert resumed["observed_tokens"] is None
    assert resumed["known_tokens"] == 10
    assert resumed["reserved_tokens"] == 0
    assert resumed["spent_token_estimate"] == 210
    assert inflight


@pytest.mark.asyncio
async def test_resume_reconciles_post_result_crash_before_admitting_another_call(tmp_path):
    tasks = [make_task(case_id="case-a"), make_task(case_id="case-b", group_id="group-b")]
    catalogs = {}
    pages = {}
    for task in tasks:
        catalogs.update(make_catalog(task))
        pages[f"{task.case_id}.json"] = make_page("https://example.test/jobs/a")
    calls = 0

    class Reader:
        def read(self, page):
            nonlocal calls
            calls += 1
            return {
                "status": "ok",
                "reading": PageReading(
                    page_type="single_job",
                    jobs=[
                        GroundedJob(
                            title=QuotedField(value="AI Agent Intern", quote="AI Agent Intern"),
                            company=QuotedField(value="Acme AI", quote="Acme AI"),
                            location=QuotedField(value="Remote", quote="Remote"),
                            requirements=["Python and LangGraph"],
                            responsibilities=["Build and evaluate AI agents"],
                            is_ai_role=True,
                            role_evidence=["Build and evaluate AI agents"],
                            is_internship=True,
                            internship_evidence=["AI Agent Intern"],
                        )
                    ],
                ).model_dump(mode="json"),
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }

    output = tmp_path / "crash-resume"
    config = BenchmarkRunConfig(
        suite="reader",
        strategies=("always",),
        dataset=tmp_path,
        split="dev",
        runs=1,
        output_dir=output,
        max_calls=1,
    )
    loader = loader_for(tasks, catalogs)
    await run_benchmark(
        config,
        dataset_loader=loader,
        reader_factory=lambda **kwargs: Reader(),
        page_resolver=pages.__getitem__,
    )
    assert calls == 1

    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["budget"] = {
        "calls": 0,
        "known_tokens": 0,
        "spent_token_estimate": 0,
        "usage_missing": 0,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    resumed = await run_benchmark(
        config.model_copy(update={"resume": True}),
        dataset_loader=loader,
        reader_factory=lambda **kwargs: Reader(),
        page_resolver=pages.__getitem__,
    )
    assert calls == 1
    assert resumed["halted_reason"] == "request_budget_exhausted"
    assert resumed["summary"]["budget"]["calls"] == 1


def test_reader_terminal_mapping_uses_evidence_then_target_and_delivery():
    exhausted = make_task(case_id="c6", allowed=())
    exhausted = exhausted.model_copy(
        update={"expected_outcome": "no_candidates", "acceptable_stop_reasons": ("candidate_exhausted",)}
    )
    shortage = make_task(case_id="c8", target=2, allowed=("job-a",))
    shortage = shortage.model_copy(
        update={"expected_outcome": "partial", "acceptable_stop_reasons": ("candidate_shortage",)}
    )
    success = make_task(case_id="c14", target=1, allowed=("job-a",))

    assert _reader_terminal_reason(
        exhausted,
        Counter({"review": 2}),
        ["not_job"],
        attempted=2,
        candidate_count=2,
        delivered=0,
    ) == "candidate_exhausted"
    assert _reader_terminal_reason(
        exhausted,
        Counter({"rejected": 2}),
        ["reader_rejected"],
        attempted=2,
        candidate_count=2,
        delivered=0,
    ) == "candidate_exhausted"
    assert _reader_terminal_reason(
        shortage,
        Counter({"accepted": 1}),
        [],
        attempted=1,
        candidate_count=1,
        delivered=1,
    ) == "candidate_shortage"
    assert _reader_terminal_reason(
        success,
        Counter({"accepted": 1}),
        [],
        attempted=1,
        candidate_count=1,
        delivered=1,
    ) == "target_reached"


def test_reader_terminal_canonicalizes_concrete_evidence_without_gold_reasons():
    task = make_task(case_id="evidence", allowed=())
    task = task.model_copy(
        update={
            "expected_outcome": "no_candidates",
            "acceptable_stop_reasons": ("candidate_exhausted",),
        }
    )
    cases = (
        (["location_conflict"], "location_conflict"),
        (["location_unknown"], "location_unknown"),
        (["model_reported_uncertainty"], "insufficient_text"),
        (["insufficient"], "insufficient_text"),
        (["recruitment_notice"], "needs_human_review"),
        (["job_list"], "needs_human_review"),
        (["missing_company"], "reader_review"),
        (["not_job"], "candidate_exhausted"),
    )

    for reasons, expected in cases:
        assert _reader_terminal_reason(
            task,
            Counter({"review": 1}),
            reasons,
            attempted=1,
            candidate_count=1,
            delivered=0,
        ) == expected


def test_reader_terminal_errors_precede_exhaustion_and_ignore_gold_reasons():
    first = make_task(case_id="error-a", allowed=())
    second = first.model_copy(
        update={
            "expected_outcome": "no_candidates",
            "acceptable_stop_reasons": ("candidate_exhausted",),
        }
    )
    third = first.model_copy(
        update={"acceptable_stop_reasons": ("location_unknown", "needs_human_review")}
    )

    for task in (first, second, third):
        assert _reader_terminal_reason(
            task,
            Counter({"error": 1}),
            ["PageTimeoutError"],
            attempted=1,
            candidate_count=1,
            delivered=0,
        ) == "PageTimeoutError"
        assert _reader_terminal_reason(
            task,
            Counter({"error": 1}),
            ["deadline_exceeded"],
            attempted=1,
            candidate_count=1,
            delivered=0,
        ) == "deadline_exceeded"
        assert _reader_terminal_reason(
            task,
            Counter({"rejected": 1}),
            ["not_job"],
            attempted=1,
            candidate_count=1,
            delivered=0,
        ) == "candidate_exhausted"


@pytest.mark.asyncio
async def test_actual_g6_removed_snapshots_reject_before_reader_and_exhaust(tmp_path):
    dataset = Path(__file__).resolve().parents[1] / "evaluations" / "model-value-v3"
    tasks, catalog, _manifest = load_model_value_dataset(dataset)
    task = next(item for item in tasks if item.case_id == "c3-g06-v1")
    task = task.model_copy(update={"split": "dev"})
    selected_catalog = {task.case_id: catalog[task.case_id]}
    output = tmp_path / "g6-removed"

    result = await run_benchmark(
        BenchmarkRunConfig(
            suite="reader",
            strategies=("rules",),
            dataset=dataset,
            split="dev",
            runs=1,
            output_dir=output,
            max_calls=10,
        ),
        dataset_loader=loader_for([task], selected_catalog),
        page_resolver=ArchivePageResolver(dataset),
    )

    prediction = read_results(output / "results.jsonl")[0]
    detail = result["episode_details"][0]
    assert prediction.terminal_reason == "candidate_exhausted"
    assert prediction.status == "executed"
    assert prediction.planner_calls == 0
    assert prediction.saved_job_keys == ()
    assert detail["reader_counts"]["rejected"] == 2
    assert detail["reader_counts"]["review"] == 0
    assert [page["reasons"] for page in detail["reader_pages"]] == [
        ["source_removed"],
        ["source_removed"],
    ]


@pytest.mark.asyncio
async def test_episode_timeout_is_isolated_and_late_admission_stays_accounted(tmp_path):
    tasks = [make_task(case_id="case-a"), make_task(case_id="case-b", group_id="group-b")]
    catalogs = {}
    pages = {}
    for task in tasks:
        catalogs.update(make_catalog(task))
        pages[f"{task.case_id}.json"] = make_page("https://example.test/jobs/a")
    calls: list[str] = []
    lock = threading.Lock()

    class Reader:
        def __init__(self, label: str) -> None:
            self.label = label

        def read(self, page):
            with lock:
                calls.append(self.label)
            if self.label == "first":
                time.sleep(0.25)
            return {
                "status": "ok",
                "reading": PageReading(
                    page_type="single_job",
                    jobs=[
                        GroundedJob(
                            title=QuotedField(value="AI Agent Intern", quote="AI Agent Intern"),
                            company=QuotedField(value="Acme AI", quote="Acme AI"),
                            location=QuotedField(value="Remote", quote="Remote"),
                            requirements=["Python and LangGraph"],
                            responsibilities=["Build and evaluate AI agents"],
                            is_ai_role=True,
                            role_evidence=["Build and evaluate AI agents"],
                            is_internship=True,
                            internship_evidence=["AI Agent Intern"],
                        )
                    ],
                ).model_dump(mode="json"),
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }

    factory_calls = 0

    def reader_factory(**kwargs):
        nonlocal factory_calls
        factory_calls += 1
        return Reader("first" if factory_calls == 1 else "second")

    output = tmp_path / "timeout-isolation"
    result = await run_benchmark(
        BenchmarkRunConfig(
            suite="reader",
            strategies=("always",),
            dataset=tmp_path,
            split="dev",
            runs=1,
            output_dir=output,
            max_calls=10,
            case_deadline_seconds=0.1,
        ),
        dataset_loader=loader_for(tasks, catalogs),
        reader_factory=reader_factory,
        page_resolver=pages.__getitem__,
    )
    await asyncio.sleep(0.3)

    assert calls == ["first", "second"]
    predictions = read_results(output / "results.jsonl")
    assert len(predictions) == 2
    assert predictions[0].status == "error"
    assert predictions[0].error == "deadline_exceeded"
    assert predictions[1].status != "error"
    journal = _budget_state_from_journal(output / "budget-journal.jsonl")
    assert journal["prior_calls"] == 2
    assert result["summary"]["budget"]["calls"] == 2


@pytest.mark.asyncio
async def test_empty_model_response_fault_is_injected_for_model_strategies(tmp_path):
    task = make_task()
    catalog = make_catalog(task)
    catalog[task.case_id]["reader_fault"] = "empty_model_response"
    inner_calls = 0

    class InnerReader:
        def read(self, page):
            nonlocal inner_calls
            inner_calls += 1
            raise AssertionError("the controlled empty response must precede transport")

    output = tmp_path / "empty-response"
    result = await run_benchmark(
        BenchmarkRunConfig(
            suite="reader",
            strategies=("always", "adaptive"),
            dataset=tmp_path,
            split="dev",
            runs=1,
            output_dir=output,
            max_calls=10,
        ),
        dataset_loader=loader_for([task], catalog),
        reader_factory=lambda **kwargs: InnerReader(),
        page_resolver=lambda ref: make_page("https://example.test/jobs/a"),
    )

    predictions = read_results(output / "results.jsonl")
    assert len(predictions) == 2
    by_strategy = {prediction.strategy: prediction for prediction in predictions}
    assert by_strategy["always"].status == "error"
    assert by_strategy["adaptive"].status == "executed"
    assert by_strategy["adaptive"].planner_calls == 0
    assert by_strategy["adaptive"].saved_job_keys == ("job-a",)
    assert inner_calls == 0
    details = {detail["strategy"]: detail for detail in result["episode_details"]}
    assert details["always"]["reader_fault_events"][0]["fault"] == "empty_model_response"
    assert details["always"]["reader_pages"][0]["error_type"] == "EmptyModelResponse"
    assert details["adaptive"]["reader_fault_events"] == []
    always = result["summary"]["strategies"]["always"]
    assert always["task_review_rate"] == always["review_rate"]
    assert always["page_error_rate"]["numerator"] == 1
    assert always["page_error_rate"]["denominator"] == 1
