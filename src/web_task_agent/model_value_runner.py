"""Fair, resumable execution for the C5 model-value comparison.

This module owns experiment orchestration only. It never fetches a page: both
suites read immutable fixture pages, and every episode receives a fresh SQLite
database, browser state, reading cache, and provider telemetry stream.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import sqlite3
import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from time import monotonic, perf_counter
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from web_task_agent.agent_components import AgentServices
from web_task_agent.agent_models import DecisionAgentState
from web_task_agent.agent_service import AgentRunOptions, start_agent_run
from web_task_agent.browser import PageHttpError, PageTimeoutError
from web_task_agent.evidence_reader import EvidenceJobReader
from web_task_agent.extractor import PageExtractor
from web_task_agent.llm_extractor import LlmProviderConfig, build_llm_provider_config
from web_task_agent.matcher import JobMatcher
from web_task_agent.model_value_dataset import current_source_code_hashes, load_model_value_dataset
from web_task_agent.model_value_evaluation import (
    STRATEGIES,
    CasePrediction,
    ModelValueTask,
    paired_group_bootstrap,
    summarize_reader_page_metrics,
    summarize_results,
    validate_dataset,
)
from web_task_agent.models import BrowserPage, JobPosting, UserProfile
from web_task_agent.planner_routing import ModelBudgetExceeded, ModelCallBudget
from web_task_agent.reading_router import (
    ReadingModelCache,
    ReadingRouter,
    RulesPageReader,
)
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.search_conditions import job_condition_reasons
from web_task_agent.source_archive import SourceArchive
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow

__all__ = [
    "ArchiveBrowser",
    "ArchivePageResolver",
    "BenchmarkRunConfig",
    "RecordingLlmTransport",
    "RunModelCallBudget",
    "ScopedModelCallBudget",
    "build_cli_parser",
    "main",
    "planned_episode_order",
    "run_benchmark",
]

Suite = Literal["planner", "reader"]
Split = Literal["dev", "test"]
Strategy = Literal["rules", "always", "adaptive"]
EpisodeKey = tuple[str, Strategy, int]

_DEFAULT_STRATEGIES: tuple[Strategy, ...] = ("rules", "always", "adaptive")
_FAULTS = frozenset({"timeout", "removed", "error"})
_RESULT_FIELDS = (
    "request_id",
    "provider",
    "model",
    "response_model",
    "prompt_hash",
    "input_hash",
    "params",
    "status",
    "elapsed_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "usage_complete",
    "timeout",
    "error_type",
    "fallback_reason",
)


class BenchmarkRunConfig(BaseModel):
    """Frozen inputs for one comparable benchmark run."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    suite: Suite
    strategies: tuple[Strategy, ...] = _DEFAULT_STRATEGIES
    dataset: Path
    split: Split
    provider: str | None = None
    model: str | None = None
    runs: int = Field(default=3, ge=1)
    seed: int = 20261023
    limit_cases: int | None = Field(default=None, ge=1)
    max_calls: int | None = Field(default=None, ge=0)
    max_planner_calls_per_case: int = Field(default=4, ge=0)
    max_observed_tokens: int = Field(default=2_000_000, ge=0)
    case_deadline_seconds: float = Field(default=60, gt=0, le=600)
    dry_run: bool = False
    validate_only: bool = False
    output_dir: Path | None = None
    resume: bool = False
    exploratory: bool = False

    @field_validator("strategies", mode="before")
    @classmethod
    def _validate_strategies(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]
        if not isinstance(value, (list, tuple)):
            raise ValueError("strategies must be a sequence")
        result = tuple(value)
        if not result:
            raise ValueError("at least one strategy is required")
        unknown = [item for item in result if item not in STRATEGIES]
        if unknown:
            raise ValueError(f"unknown strategies: {', '.join(map(str, unknown))}")
        if len(set(result)) != len(result):
            raise ValueError("strategies must not contain duplicates")
        return result

    @model_validator(mode="after")
    def _validate_modes(self) -> BenchmarkRunConfig:
        if self.dry_run and self.validate_only:
            raise ValueError("--dry-run and --validate-only are mutually exclusive")
        if self.split == "test" and self.limit_cases is not None:
            raise ValueError("--limit-cases is forbidden for split=test")
        if self.split == "test" and self.exploratory:
            raise ValueError("--exploratory is forbidden for split=test")
        if self.resume and self.output_dir is None:
            raise ValueError("--resume requires --output-dir")
        if not self.dry_run and not self.validate_only and self.output_dir is None:
            raise ValueError("a live benchmark requires --output-dir")
        return self

    @property
    def effective_max_calls(self) -> int:
        if self.max_calls is not None:
            return self.max_calls
        return 1000 if self.suite == "planner" else 240

    def fingerprint_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        for key in ("dry_run", "validate_only", "output_dir", "resume"):
            payload.pop(key, None)
        payload["dataset"] = str(self.dataset.resolve())
        payload["effective_max_calls"] = self.effective_max_calls
        return payload

    @property
    def fingerprint(self) -> str:
        return _sha256_json(self.fingerprint_payload())


@dataclass(frozen=True)
class _CatalogView:
    candidate_urls: tuple[str, ...]
    candidate_summaries: dict[str, dict[str, Any]]
    job_key_by_url: dict[str, str]
    faults: dict[str, tuple[str, ...]]
    max_steps: int
    snapshot_refs: tuple[str, ...]
    snapshot_ref_by_url: dict[str, str]
    reader_fault: str | None


@dataclass(frozen=True)
class _ResolvedCase:
    pages_by_candidate: dict[str, BrowserPage]
    aliases: dict[str, str]
    authorized_urls: frozenset[str]
    missing_refs: tuple[str, ...]


def _sha256_json(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(_json_value(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_relative_ref(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or path.parts[0] == ".":
        raise ValueError(f"unsafe snapshot ref: {value!r}")
    return path.as_posix()


def _catalog_view(task: ModelValueTask, raw: Mapping[str, Any]) -> _CatalogView:
    candidate_urls = tuple(dict.fromkeys(str(value).strip() for value in raw.get("candidate_urls", [])))
    if any(not value for value in candidate_urls):
        raise ValueError(f"case {task.case_id!r} has an empty candidate URL")
    summaries_raw = raw.get("candidate_summaries", {})
    if not isinstance(summaries_raw, Mapping):
        raise ValueError(f"case {task.case_id!r} candidate_summaries must be a mapping")
    summaries = {
        str(url): dict(value) if isinstance(value, Mapping) else {}
        for url, value in summaries_raw.items()
    }
    key_raw = raw.get("job_key_by_url", {})
    if not isinstance(key_raw, Mapping):
        raise ValueError(f"case {task.case_id!r} job_key_by_url must be a mapping")
    job_keys = {str(url): str(key).strip() for url, key in key_raw.items()}
    if any(not key for key in job_keys.values()):
        raise ValueError(f"case {task.case_id!r} has an empty stable job key")
    faults_raw = raw.get("faults", {})
    if not isinstance(faults_raw, Mapping):
        raise ValueError(f"case {task.case_id!r} faults must be a mapping")
    faults: dict[str, tuple[str, ...]] = {}
    for url, queue in faults_raw.items():
        if not isinstance(queue, (list, tuple)):
            raise ValueError(f"case {task.case_id!r} fault queue must be a sequence")
        clean = tuple(str(item) for item in queue)
        unknown = [item for item in clean if item not in _FAULTS]
        if unknown:
            raise ValueError(f"case {task.case_id!r} has unknown faults: {unknown}")
        faults[str(url)] = clean
    reader_fault = raw.get("reader_fault")
    if reader_fault is not None and reader_fault != "empty_model_response":
        raise ValueError(f"case {task.case_id!r} has an unknown reader fault: {reader_fault!r}")
    max_steps = raw.get("max_steps")
    if max_steps is None:
        max_steps = min(400, max(12, 6 * task.target_count + 8))
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 1 <= max_steps <= 400:
        raise ValueError(f"case {task.case_id!r} max_steps must be between 1 and 400")
    refs_raw = raw.get("snapshot_refs")
    refs = tuple(task.snapshot_refs) if refs_raw is None else tuple(str(item) for item in refs_raw)
    refs = tuple(_safe_relative_ref(item) for item in refs)
    snapshots_raw = raw.get("snapshot_by_url")
    if snapshots_raw is None:
        raise ValueError(f"case {task.case_id!r} snapshot_by_url must be a mapping")
    elif isinstance(snapshots_raw, Mapping):
        snapshot_ref_by_url = {
            str(url): _safe_relative_ref(str(ref))
            for url, ref in snapshots_raw.items()
        }
    else:
        raise ValueError(f"case {task.case_id!r} snapshot_by_url must be a mapping")
    missing_urls = sorted(set(candidate_urls) - set(snapshot_ref_by_url))
    unknown_urls = sorted(set(snapshot_ref_by_url) - set(candidate_urls))
    if missing_urls or unknown_urls:
        raise ValueError(
            f"case {task.case_id!r} snapshot_by_url must exactly cover candidate URLs; "
            f"missing={missing_urls}, unknown={unknown_urls}"
        )
    return _CatalogView(
        candidate_urls=candidate_urls,
        candidate_summaries=summaries,
        job_key_by_url=job_keys,
        faults=faults,
        max_steps=max_steps,
        snapshot_refs=refs,
        snapshot_ref_by_url=snapshot_ref_by_url,
        reader_fault=str(reader_fault) if reader_fault is not None else None,
    )


class RunModelCallBudget(ModelCallBudget):
    """Shared billable-request budget with resumable accounting."""

    def __init__(
        self,
        *,
        max_calls: int,
        max_observed_tokens: int,
        deadline_seconds: float,
        prior_calls: int = 0,
        prior_spent_tokens: int = 0,
        prior_usage_missing: int = 0,
        prior_known_tokens: int = 0,
        journal_path: Path | None = None,
    ) -> None:
        if min(
            max_calls,
            max_observed_tokens,
            prior_calls,
            prior_spent_tokens,
            prior_usage_missing,
            prior_known_tokens,
        ) < 0:
            raise ValueError("budget counters must not be negative")
        if prior_known_tokens > prior_spent_tokens:
            raise ValueError("known tokens cannot exceed the conservative prior spend")
        self.configured_max_calls = max_calls
        self.configured_max_observed_tokens = max_observed_tokens
        self.prior_calls = prior_calls
        self.prior_spent_tokens = prior_spent_tokens
        self.prior_usage_missing = prior_usage_missing
        self.prior_known_tokens = prior_known_tokens
        self.journal_path = Path(journal_path) if journal_path is not None else None
        self._journal_lock = threading.Lock()
        super().__init__(
            max_calls=max(0, max_calls - prior_calls),
            max_observed_tokens=max(0, max_observed_tokens - prior_spent_tokens),
            deadline_seconds=deadline_seconds,
        )

    @property
    def total_calls(self) -> int:
        return self.prior_calls + self.calls

    def _journal(self, payload: Mapping[str, Any]) -> None:
        if self.journal_path is None:
            return
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n"
        with self._journal_lock:
            with self.journal_path.open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())

    def reserve(self, *, estimated_tokens: int) -> str:
        reservation = super().reserve(estimated_tokens=estimated_tokens)
        # Admission is durable before the transport can issue the request. A
        # crash after this line is reconciled as an unknown in-flight call.
        self._journal(
            {
                "event": "admit",
                "reservation": reservation,
                "estimated_tokens": estimated_tokens,
            }
        )
        return reservation

    def finish(self, reservation: str, *, usage: dict | None) -> None:
        super().finish(reservation, usage=usage)
        total = usage.get("total_tokens") if isinstance(usage, dict) else None
        known_total = (
            total
            if isinstance(total, int) and not isinstance(total, bool) and total >= 0
            else None
        )
        self._journal(
            {
                "event": "settle",
                "reservation": reservation,
                "total_tokens": known_total,
            }
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            reserved = sum(self._reservations.values())
            current_unknown = self._unknown_reservations
            current_known = self._known_tokens
            calls = self.calls
        usage_missing = self.prior_usage_missing + self.usage_missing
        known_tokens = self.prior_known_tokens + current_known
        observed = None if usage_missing else known_tokens
        return {
            "max_calls": self.configured_max_calls,
            "calls": self.prior_calls + calls,
            "remaining_calls": max(0, self.configured_max_calls - self.prior_calls - calls),
            "max_observed_tokens": self.configured_max_observed_tokens,
            "observed_tokens": observed,
            "known_tokens": known_tokens,
            "usage_missing": usage_missing,
            "reserved_tokens": reserved,
            "spent_token_estimate": self.prior_spent_tokens + current_known + current_unknown + reserved,
        }


class ScopedModelCallBudget:
    """Per-episode admission and deadline in front of one shared budget."""

    def __init__(
        self,
        shared: RunModelCallBudget,
        *,
        max_calls: int,
        deadline_seconds: float,
        blocked: Callable[[], bool] | None = None,
    ) -> None:
        if max_calls < 0 or deadline_seconds <= 0:
            raise ValueError("invalid scoped budget")
        self.shared = shared
        self.max_calls = max_calls
        self.deadline = monotonic() + deadline_seconds
        self.calls = 0
        self._blocked = blocked or (lambda: False)
        self._lock = threading.Lock()

    def reserve(self, *, estimated_tokens: int) -> str:
        with self._lock:
            if self.calls >= self.max_calls or monotonic() >= self.deadline or self._blocked():
                raise ModelBudgetExceeded("model request budget exhausted")
            reservation = self.shared.reserve(estimated_tokens=estimated_tokens)
            self.calls += 1
            return reservation

    def finish(self, reservation: str, *, usage: dict | None) -> None:
        self.shared.finish(reservation, usage=usage)


class RecordingLlmTransport:
    """Record hashes and metadata for a chat transport without prompt bodies."""

    def __init__(
        self,
        transport: Callable[[str, dict[str, str], dict[str, Any], int], dict[str, Any]],
        *,
        provider: str,
        model: str,
    ) -> None:
        self.transport = transport
        self.provider = provider
        self.model = model
        self.records: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    @property
    def failures(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(record) for record in self.records if record.get("status") == "error"]

    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        started = perf_counter()
        messages = payload.get("messages", [])
        user_input = ""
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, Mapping) and message.get("role") == "user":
                    user_input = str(message.get("content", ""))
        params = _json_value({key: value for key, value in payload.items() if key != "messages"})
        record: dict[str, Any] = {
            "request_id": uuid4().hex,
            "provider": self.provider,
            "model": self.model,
            "response_model": None,
            "prompt_hash": _sha256_json(payload),
            "input_hash": hashlib.sha256(user_input.encode("utf-8")).hexdigest(),
            "params": params,
            "status": "error",
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "usage_complete": False,
        }
        try:
            response = self.transport(url, headers, payload, timeout_seconds)
            usage = response.get("usage") if isinstance(response, Mapping) else None
            if isinstance(usage, Mapping):
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    value = usage.get(key)
                    record[key] = (
                        value
                        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                        else None
                    )
                record["usage_complete"] = all(record[key] is not None for key in (
                    "prompt_tokens", "completion_tokens", "total_tokens"
                ))
            record["response_model"] = response.get("model", self.model)
            record["status"] = "ok"
            return response
        except Exception as error:
            record["error_type"] = type(error).__name__
            record["timeout"] = isinstance(error, (TimeoutError, asyncio.CancelledError))
            raise
        finally:
            record["elapsed_ms"] = (perf_counter() - started) * 1000
            with self._lock:
                self.records.append(record)


class _EmptyModelResponseReader:
    """Controlled model fault: return no reading before any transport call."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls = 0

    def read(self, page: BrowserPage) -> dict[str, Any]:
        self.calls += 1
        return {"status": "error", "error_type": "EmptyModelResponse", "usage": {}}


class ArchivePageResolver:
    """Resolve exact relative snapshot refs without network access."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def __call__(self, ref: str) -> BrowserPage:
        safe = _safe_relative_ref(ref)
        candidate_paths = [self.root / PurePosixPath(safe)]
        if not pure_path_has_parent(safe):
            candidate_paths.append(self.root / "snapshots" / f"{safe}.json")
        path = next((item for item in candidate_paths if item.is_file()), None)
        if path is None:
            raise FileNotFoundError(f"snapshot ref is missing: {ref}")
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError(f"snapshot ref escapes archive root: {ref}")
        raw = _read_json(resolved)
        if not isinstance(raw, Mapping):
            raise ValueError(f"snapshot ref is not a JSON object: {ref}")
        nested = raw.get("page")
        if isinstance(nested, Mapping):
            raw = nested
        if "url" in raw and "content" in raw:
            return BrowserPage.model_validate(raw)
        snapshot_id = raw.get("snapshot_id")
        if not isinstance(snapshot_id, str) and PurePosixPath(safe).parent.name == "snapshots":
            snapshot_id = PurePosixPath(safe).stem
        if isinstance(snapshot_id, str) and snapshot_id.strip():
            archive_root = resolved.parent.parent if resolved.parent.name == "snapshots" else self.root
            return SourceArchive(archive_root).read_page(snapshot_id)
        raise ValueError(f"snapshot ref does not contain a page or snapshot identity: {ref}")


def pure_path_has_parent(value: str) -> bool:
    return len(PurePosixPath(value).parts) > 1


class ArchiveBrowser:
    """Browser protocol over immutable pages with sequential controlled faults."""

    def __init__(
        self,
        *,
        pages_by_candidate: Mapping[str, BrowserPage],
        candidate_urls: Sequence[str],
        candidate_summaries: Mapping[str, Mapping[str, Any]],
        faults: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.candidate_urls = list(dict.fromkeys(candidate_urls))
        self._candidate_pages = {
            url: page.model_copy(deep=True) for url, page in pages_by_candidate.items()
        }
        self._pages_by_url = dict(self._candidate_pages)
        for page in self._candidate_pages.values():
            self._pages_by_url.setdefault(page.url, page.model_copy(deep=True))
        self.candidate_summaries = {
            url: copy.deepcopy(dict(summary)) for url, summary in candidate_summaries.items()
        }
        self._faults = {url: list(queue) for url, queue in (faults or {}).items()}
        self._fault_index: Counter[str] = Counter()
        self.fault_events: list[dict[str, Any]] = []

    async def search(self, query: str, target_count: int) -> list[BrowserPage]:
        if target_count <= 0 or not self.candidate_urls:
            return []
        return [
            BrowserPage(
                url="fixture://candidates",
                title="Frozen candidate list",
                content="Frozen benchmark candidate metadata; no page was fetched.",
                source="fixture",
                metadata={"candidate_urls": self.candidate_urls[:target_count]},
            )
        ]

    async def open_url(self, url: str) -> BrowserPage:
        queue = self._faults.get(url, [])
        index = self._fault_index[url]
        if index < len(queue):
            self._fault_index[url] += 1
            fault = queue[index]
            self.fault_events.append({"url": url, "fault": fault, "sequence": index + 1})
            if fault == "timeout":
                raise PageTimeoutError("controlled timeout")
            if fault == "removed":
                raise PageHttpError("controlled removal", status_code=410)
            raise RuntimeError("controlled page error")
        page = self._pages_by_url.get(url)
        if page is None:
            raise FileNotFoundError("frozen snapshot is missing for candidate")
        return page.model_copy(deep=True)

    def finalize_metadata(self, run_dir: Path) -> dict[str, Any]:
        return {
            "fixture_candidate_count": len(self.candidate_urls),
            "fixture_fault_events": copy.deepcopy(self.fault_events),
        }


class _PlannerTelemetryAdapter:
    def __init__(self, planner: Any, *, provider: str, model: str) -> None:
        self.planner = planner
        telemetry = getattr(planner, "telemetry", None)
        if telemetry is None or not hasattr(telemetry, "requests"):
            telemetry = type("PlannerTelemetry", (), {"requests": []})()
            planner.telemetry = telemetry
        self.telemetry = telemetry
        self.provider = provider
        self.model = model

    async def decide(self, state: DecisionAgentState):
        before = len(self.telemetry.requests)
        try:
            return await self.planner.decide(state)
        finally:
            for record in self.telemetry.requests[before:]:
                record.setdefault("provider", self.provider)
                record.setdefault("model", self.model)
                record.setdefault(
                    "params",
                    {"temperature": 0, "max_tokens": 600, "response_format": {"type": "json_object"}},
                )


DatasetLoader = Callable[..., tuple[Sequence[ModelValueTask], Mapping[str, Mapping[str, Any]], Mapping[str, Any]]]
PlannerFactory = Callable[..., Any]
ReaderFactory = Callable[..., Any]
PageResolver = Callable[[str], BrowserPage]


def planned_episode_order(
    tasks: Sequence[ModelValueTask],
    strategies: Sequence[str],
    *,
    runs: int,
    seed: int,
    suite: str,
) -> list[EpisodeKey]:
    """Rotate strategy order within each paired case/round block."""

    selected = tuple(strategy for strategy in strategies if strategy in STRATEGIES)
    if not selected:
        raise ValueError("at least one strategy is required")
    result: list[EpisodeKey] = []
    for task in tasks:
        for round_number in range(1, runs + 1):
            digest = hashlib.sha256(
                f"{seed}\0{suite}\0{task.case_id}\0{round_number}".encode()
            ).digest()
            offset = int.from_bytes(digest[:8], "big") % len(selected)
            rotated = selected[offset:] + selected[:offset]
            result.extend((task.case_id, strategy, round_number) for strategy in rotated)
    return result


def _dataset_root_from_manifest(root: Path, manifest: Mapping[str, Any]) -> Path:
    value = manifest.get("archive_root") or manifest.get("parent_archive")
    path = Path(str(value)) if value else root
    return path if path.is_absolute() else root / path


def _manifest_payload(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = manifest.get("payload")
    return payload if isinstance(payload, Mapping) and "sha256" in manifest else manifest


def _resolve_case(
    task: ModelValueTask,
    catalog: _CatalogView,
    resolver: PageResolver,
    keyword: str,
    location: str,
) -> tuple[_ResolvedCase, UserProfile]:
    pages: dict[str, BrowserPage] = {}
    aliases: dict[str, str] = {}
    authorized = set(catalog.candidate_urls)
    missing: list[str] = []
    for url in catalog.candidate_urls:
        ref = catalog.snapshot_ref_by_url.get(url)
        if ref is None:
            missing.append(url)
            continue
        page = resolver(ref)
        if page.url != url:
            raise ValueError(
                f"snapshot URL {page.url!r} does not match candidate URL {url!r}"
            )
        pages[url] = page
        key = catalog.job_key_by_url.get(url, f"unauthorized:{url}")
        aliases[url] = key
        aliases[page.url] = key
        authorized.add(page.url)
        canonical = page.metadata.get("canonical_url")
        if isinstance(canonical, str) and canonical:
            aliases[canonical] = key
            authorized.add(canonical)
    profile = UserProfile(
        keyword=keyword,
        location=location,
        target_count=task.target_count,
        seed_urls=list(catalog.candidate_urls),
    )
    return (
        _ResolvedCase(
            pages_by_candidate=pages,
            aliases=aliases,
            authorized_urls=frozenset(authorized),
            missing_refs=tuple(missing),
        ),
        profile,
    )


def _profile_fields(task: ModelValueTask, catalog_raw: Mapping[str, Any]) -> tuple[str, str]:
    conditions = task.conditions
    profile = catalog_raw.get("profile")
    profile = profile if isinstance(profile, Mapping) else {}
    keyword = (
        conditions.get("keyword")
        or conditions.get("query")
        or profile.get("keyword")
        or profile.get("query")
    )
    location = conditions.get("location") or profile.get("location")
    if location is None:
        locations = conditions.get("locations")
        if isinstance(locations, (list, tuple)) and locations:
            location = locations[0]
    if not isinstance(keyword, str) or not keyword.strip():
        raise ValueError(f"case {task.case_id!r} is missing a search keyword")
    if not isinstance(location, str) or not location.strip():
        raise ValueError(f"case {task.case_id!r} is missing a search location")
    return keyword.strip(), location.strip()


def _sanitize_provider_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    for record in records:
        item = {key: _json_value(record[key]) for key in _RESULT_FIELDS if key in record}
        clean.append(item)
    return clean


def _provider_records(owner: Any) -> list[dict[str, Any]]:
    telemetry = getattr(owner, "telemetry", None)
    records = getattr(telemetry, "records", None) if telemetry is not None else None
    if records is None and telemetry is not None:
        records = getattr(telemetry, "requests", [])
    if records is None:
        records = []
    return _sanitize_provider_records(records)


def _usage_summary(
    records: Sequence[Mapping[str, Any]], *, calls: int
) -> tuple[int | None, int | None, int | None, bool]:
    complete = []
    for record in records:
        values = [record.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")]
        if all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in values):
            complete.append((values[0], values[1], values[2]))
        else:
            return None, None, None, False
    if calls == 0 and not complete:
        return 0, 0, 0, True
    if len(complete) < calls:
        return None, None, None, False
    return (
        sum(item[0] for item in complete),
        sum(item[1] for item in complete),
        sum(item[2] for item in complete),
        True,
    )


def _db_jobs(db_path: Path) -> list[JobPosting]:
    if not db_path.is_file():
        return []
    try:
        return JobRepository(db_path).list_jobs()
    except (OSError, sqlite3.Error, ValueError):
        return []


def _side_effects(
    jobs: Sequence[JobPosting],
    resolved: _ResolvedCase,
    catalog: _CatalogView,
    *,
    keyword: str,
    location: str,
) -> tuple[tuple[str, ...], int, int, int]:
    keys = [
        resolved.aliases.get(
            job.url, catalog.job_key_by_url.get(job.url, f"unauthorized:{job.url}")
        )
        for job in jobs
    ]
    duplicates = len(keys) - len(set(keys))
    unauthorized = sum(1 for job in jobs if job.url not in resolved.authorized_urls)
    violations = 0
    verifier = JobVerifier()
    for job in jobs:
        reasons = list(verifier.verify(job).reasons)
        reasons.extend(job_condition_reasons(keyword, location, job))
        if reasons:
            violations += 1
    return tuple(keys), unauthorized, duplicates, violations


def _error_prediction(
    case_id: str,
    strategy: Strategy,
    round_number: int,
    *,
    elapsed_ms: float,
    error: str,
    saved: Sequence[str] = (),
    calls: int = 0,
    steps: int = 0,
    unauthorized: int = 0,
    duplicates: int = 0,
    violations: int = 0,
) -> CasePrediction:
    return CasePrediction(
        case_id=case_id,
        strategy=strategy,
        round=round_number,
        delivered_job_keys=list(saved),
        saved_job_keys=list(saved),
        terminal_status="failed",
        terminal_reason="runner_error",
        planner_calls=calls,
        consumed_steps=steps,
        elapsed_ms=max(0.0, elapsed_ms),
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
        usage_complete=False,
        unauthorized_writes=unauthorized,
        duplicate_writes=duplicates,
        constraint_violations=violations,
        error=error,
        status="error",
    )


class _Runner:
    def __init__(
        self,
        config: BenchmarkRunConfig,
        tasks: Sequence[ModelValueTask],
        catalogs: Mapping[str, _CatalogView],
        catalog_raw: Mapping[str, Mapping[str, Any]],
        manifest: Mapping[str, Any],
        resolver: PageResolver,
        *,
        planner_factory: PlannerFactory | None,
        reader_factory: ReaderFactory | None,
    ) -> None:
        self.config = config
        self.tasks = list(tasks)
        self.catalogs = dict(catalogs)
        self.catalog_raw = catalog_raw
        self.manifest = manifest
        self.resolver = resolver
        self.planner_factory = planner_factory
        self.reader_factory = reader_factory
        self.output = Path(config.output_dir) if config.output_dir is not None else None
        self._details: dict[str, dict[str, Any]] = {}
        self._existing_details: dict[str, dict[str, Any]] = {}
        self.prior_budget: dict[str, Any] = {}
        self.resumed_keys = 0
        self.halted_reason: str | None = None
        self._run_id = f"model-value-{uuid4().hex}"
        self._created_at = datetime.now(UTC).isoformat()
        self.budget = RunModelCallBudget(
            max_calls=config.effective_max_calls,
            max_observed_tokens=config.max_observed_tokens,
            deadline_seconds=max(3600, config.case_deadline_seconds * len(tasks) * max(1, config.runs)),
        )

    @property
    def model_dispatch_blocked(self) -> bool:
        return False

    def key(self, case_id: str, strategy: Strategy, round_number: int) -> str:
        return f"{self.config.suite}|{case_id}|{strategy}|{round_number}"

    async def execute(self, *, existing: Sequence[CasePrediction]) -> dict[str, Any]:
        assert self.output is not None
        order = planned_episode_order(
            self.tasks,
            self.config.strategies,
            runs=self.config.runs,
            seed=self.config.seed,
            suite=self.config.suite,
        )
        planned = set(order)
        completed = {(item.case_id, item.strategy, item.round) for item in existing}
        unexpected = sorted(completed - planned)
        if unexpected:
            raise ValueError(f"resume results contain keys outside the frozen plan: {unexpected}")
        self.resumed_keys = len(completed)
        self._existing_details = {
            self.key(*item): self._existing_details[self.key(*item)]
            for item in completed
            if self.key(*item) in self._existing_details
        }
        predictions = list(existing)
        self._write_artifacts(predictions)
        for case_id, strategy, round_number in order:
            key = (case_id, strategy, round_number)
            if key in completed:
                continue
            if strategy != "rules" and self.budget.total_calls >= self.config.effective_max_calls:
                self.halted_reason = "request_budget_exhausted"
                break
            task = next(item for item in self.tasks if item.case_id == case_id)
            catalog = self.catalogs[case_id]
            keyword, location = _profile_fields(task, self.catalog_raw[case_id])
            scoped = ScopedModelCallBudget(
                self.budget,
                max_calls=self.config.max_planner_calls_per_case if self.config.suite == "planner"
                else min(self.config.max_planner_calls_per_case, max(1, len(catalog.candidate_urls))),
                deadline_seconds=self.config.case_deadline_seconds,
            )
            started = perf_counter()
            detail: dict[str, Any] = {
                "suite": self.config.suite,
                "case_id": case_id,
                "strategy": strategy,
                "round": round_number,
                "source_refs": list(catalog.snapshot_refs),
            }
            try:
                if self.config.suite == "planner":
                    prediction, evidence = await self._planner_episode(
                        task, catalog, keyword, location, strategy, round_number, scoped
                    )
                else:
                    prediction, evidence = await self._reader_episode(
                        task, catalog, keyword, location, strategy, round_number, scoped
                    )
            except TimeoutError as error:
                prediction = _error_prediction(
                    case_id, strategy, round_number,
                    elapsed_ms=(perf_counter() - started) * 1000,
                    error=type(error).__name__, calls=scoped.calls,
                )
                evidence = {"timeout": True, "provider_records": [], "provider_failures": []}
            except Exception as error:
                prediction = _error_prediction(
                    case_id, strategy, round_number,
                    elapsed_ms=(perf_counter() - started) * 1000,
                    error=type(error).__name__, calls=scoped.calls,
                )
                evidence = {"provider_records": [], "provider_failures": []}
            detail.update(evidence)
            detail.update(
                {
                    "status": prediction.status,
                    "terminal_status": prediction.terminal_status,
                    "terminal_reason": prediction.terminal_reason,
                    "saved_job_keys": list(prediction.saved_job_keys),
                    "elapsed_ms": prediction.elapsed_ms,
                    "model_calls": prediction.planner_calls,
                    "total_tokens": prediction.total_tokens,
                    "error": prediction.error,
                }
            )
            self._details[self.key(case_id, strategy, round_number)] = detail
            predictions.append(prediction)
            self._append_result(prediction)
            completed.add(key)
            self._write_artifacts(predictions)
        self._write_artifacts(predictions)
        return self._result_payload(predictions)

    async def _planner_episode(
        self,
        task: ModelValueTask,
        catalog: _CatalogView,
        keyword: str,
        location: str,
        strategy: Strategy,
        round_number: int,
        budget: ScopedModelCallBudget,
    ) -> tuple[CasePrediction, dict[str, Any]]:
        assert self.output is not None
        resolved, profile = _resolve_case(task, catalog, self.resolver, keyword, location)
        if resolved.missing_refs:
            raise FileNotFoundError("snapshot refs are missing for one or more candidates")
        episode_dir = self._episode_dir(task.case_id, strategy, round_number)
        db_path = episode_dir / "business.sqlite"
        repository = JobRepository(db_path)
        repository.initialize()
        browser = ArchiveBrowser(
            pages_by_candidate=resolved.pages_by_candidate,
            candidate_urls=catalog.candidate_urls,
            candidate_summaries=catalog.candidate_summaries,
            faults=catalog.faults,
        )
        workflow = WebTaskWorkflow(
            browser=browser,
            extractor=PageExtractor(),
            matcher=JobMatcher(),
            verifier=JobVerifier(),
            repository=repository,
            reporter=MarkdownReporter(episode_dir / "reports"),
            enforce_search_conditions=True,
        )
        planner = None
        if strategy != "rules":
            if self.planner_factory is None:
                raise RuntimeError("a planner factory is required for model strategies")
            planner = _PlannerTelemetryAdapter(
                self.planner_factory(
                    provider=self.config.provider,
                    model=self.config.model,
                    budget=budget,
                    timeout_seconds=max(1, int(min(20, self.config.case_deadline_seconds))),
                ),
                provider=self.config.provider or "unknown",
                model=self.config.model or "unknown",
            )
        planner_strategy = "policy" if strategy == "rules" else strategy
        options = AgentRunOptions(
            engine="hybrid",
            planner_strategy=planner_strategy,
            planner_provider=self.config.provider if strategy != "rules" else None,
            planner_model=self.config.model if strategy != "rules" else None,
            max_steps=catalog.max_steps,
            deadline_seconds=max(0.001, min(600.0, self.config.case_deadline_seconds)),
            max_planner_calls=self.config.max_planner_calls_per_case,
            planner_timeout_seconds=max(0.001, min(20.0, self.config.case_deadline_seconds)),
        )
        services = AgentServices(
            workflow=workflow,
            run_dir=episode_dir,
            planner=planner,
            model_budget=budget,
        )
        started = perf_counter()
        state: DecisionAgentState | None = None
        result_status = "failed"
        error = ""
        try:
            result = await asyncio.wait_for(
                start_agent_run(
                    profile,
                    run_id=(
                        "mv-"
                        + hashlib.sha256(
                            (task.case_id + strategy + str(round_number)).encode()
                        ).hexdigest()[:16]
                    ),
                    owner_id="model-value-runner",
                    services=services,
                    options=options,
                ),
                timeout=max(0.001, min(600.0, self.config.case_deadline_seconds)),
            )
            state = result.agent_state
            result_status = result.status
        except TimeoutError:
            error = "deadline_exceeded"
        except Exception as failure:
            error = type(failure).__name__
        jobs = repository.list_jobs()
        keys, unauthorized, duplicates, violations = _side_effects(
            jobs, resolved, catalog, keyword=keyword, location=location
        )
        records = _provider_records(planner) if planner is not None else []
        calls = budget.calls
        prompt_tokens, completion_tokens, total_tokens, complete = _usage_summary(records, calls=calls)
        status = "error" if error else "executed"
        if error:
            prediction = _error_prediction(
                task.case_id, strategy, round_number,
                elapsed_ms=(perf_counter() - started) * 1000, error=error,
                saved=keys, calls=calls, steps=state.budget.consumed_steps if state else 0,
                unauthorized=unauthorized, duplicates=duplicates, violations=violations,
            )
        else:
            prediction = CasePrediction(
                case_id=task.case_id,
                strategy=strategy,
                round=round_number,
                delivered_job_keys=list(keys),
                saved_job_keys=list(keys),
                terminal_status=state.terminal_status if state is not None else result_status,
                terminal_reason=state.terminal_reason if state is not None else "runner_finished",
                planner_calls=calls,
                consumed_steps=state.budget.consumed_steps if state is not None else 0,
                elapsed_ms=(perf_counter() - started) * 1000,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                usage_complete=complete,
                unauthorized_writes=unauthorized,
                duplicate_writes=duplicates,
                constraint_violations=violations,
                error="",
                status=status,
            )
        evidence = {
            "fault_events": browser.fault_events,
            "provider_records": records,
            "provider_failures": [item for item in records if item.get("status") == "error"],
            "planner_routes": copy.deepcopy(state.planner_routes) if state is not None else [],
            "planner_metrics": state.metrics.model_dump(mode="json") if state is not None else {},
            "db_job_count": len(jobs),
            "sqlite": db_path.relative_to(episode_dir.parent.parent).as_posix(),
        }
        return prediction, evidence

    async def _reader_episode(
        self,
        task: ModelValueTask,
        catalog: _CatalogView,
        keyword: str,
        location: str,
        strategy: Strategy,
        round_number: int,
        budget: ScopedModelCallBudget,
    ) -> tuple[CasePrediction, dict[str, Any]]:
        resolved, _profile = _resolve_case(task, catalog, self.resolver, keyword, location)
        episode_dir = self._episode_dir(task.case_id, strategy, round_number)
        db_path = episode_dir / "business.sqlite"
        repository = JobRepository(db_path)
        repository.initialize()
        model_reader = None
        if strategy != "rules":
            if self.reader_factory is None:
                raise RuntimeError("a reader factory is required for model strategies")
            model_reader = self.reader_factory(
                provider=self.config.provider,
                model=self.config.model,
                budget=budget,
                timeout_seconds=max(1, int(min(60, self.config.case_deadline_seconds))),
            )
            if catalog.reader_fault == "empty_model_response":
                model_reader = _EmptyModelResponseReader(model_reader)
        router = ReadingRouter(
            model_reader,
            rules_reader=RulesPageReader(PageExtractor()),
            cache=ReadingModelCache(),
            budget=budget,
        )
        counts: Counter[str] = Counter()
        reasons: list[str] = []
        route_reasons: list[str] = []
        page_records: list[dict[str, Any]] = []
        usage_records: list[dict[str, Any]] = []
        saved_logical_keys: set[str] = set()
        duplicate_delivery_suppressed = 0
        started = perf_counter()
        steps = 0
        timeout = False
        reader_fault_events: list[dict[str, Any]] = []
        processed_candidates = 0
        for url in catalog.candidate_urls[: catalog.max_steps]:
            processed_candidates += 1
            page = resolved.pages_by_candidate.get(url)
            if page is None:
                counts["error"] += 1
                reasons.append("missing_snapshot")
                continue
            if _known_removed_page(page):
                steps += 1
                counts["rejected"] += 1
                reasons.append("source_removed")
                route_reasons.append("known_removed_page")
                page_records.append(
                    {
                        "url": url,
                        "status": "rejected",
                        "reasons": ["source_removed"],
                        "route_reason": "known_removed_page",
                        "model_called": False,
                        "model_reused": False,
                        "error_type": None,
                        "elapsed_seconds": 0.0,
                    }
                )
                continue
            remaining = budget.deadline - monotonic()
            if remaining <= 0:
                counts["error"] += 1
                reasons.append("deadline_exceeded")
                timeout = True
                break
            steps += 1
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(router.read, page, keyword, location, strategy),
                    timeout=remaining,
                )
            except TimeoutError:
                counts["error"] += 1
                reasons.append("deadline_exceeded")
                timeout = True
                break
            except Exception as error:
                counts["error"] += 1
                reasons.append(type(error).__name__)
                continue
            counts[result.status] += 1
            if catalog.reader_fault == "empty_model_response" and result.model_called:
                reader_fault_events.append(
                    {"url": url, "fault": "empty_model_response", "sequence": len(reader_fault_events) + 1}
                )
            reasons.extend(result.reasons)
            route_reasons.append(result.route_reason)
            page_records.append(
                {
                    "url": url,
                    "status": result.status,
                    "reasons": list(result.reasons),
                    "route_reason": result.route_reason,
                    "model_called": result.model_called,
                    "model_reused": result.model_reused,
                    "error_type": result.error_type,
                    "elapsed_seconds": result.elapsed_seconds,
                }
            )
            if result.model_called:
                usage_records.append(dict(result.usage))
            if result.status == "accepted":
                for payload in result.jobs:
                    job_key = catalog.job_key_by_url.get(url)
                    if job_key is None:
                        counts["error"] += 1
                        reasons.append("missing_job_key")
                        continue
                    if job_key in saved_logical_keys:
                        duplicate_delivery_suppressed += 1
                        continue
                    repository.save_jobs([JobPosting.model_validate(payload)])
                    saved_logical_keys.add(job_key)
        jobs = repository.list_jobs()
        keys, unauthorized, duplicates, violations = _side_effects(
            jobs, resolved, catalog, keyword=keyword, location=location
        )
        records = _provider_records(model_reader) if model_reader is not None else []
        prompt_tokens, completion_tokens, total_tokens, complete = _usage_summary(
            usage_records, calls=budget.calls
        )
        terminal_reason = _reader_terminal_reason(
            task,
            counts,
            reasons,
            attempted=processed_candidates,
            candidate_count=min(len(catalog.candidate_urls), catalog.max_steps),
            delivered=len(keys),
        )
        if timeout:
            terminal_status = "error"
            status = "error"
            error = "deadline_exceeded"
        elif counts["error"]:
            terminal_status = "error"
            status = "error"
            error = next(
                (
                    item
                    for item in reasons
                    if item == "deadline_exceeded" or item.endswith("Error")
                ),
                "reader_error",
            )
        elif counts["review"]:
            terminal_status = "review"
            status = "executed"
            error = ""
        elif jobs:
            terminal_status = "completed" if len(keys) >= task.target_count else "partial"
            status = "executed"
            error = ""
        else:
            terminal_status = "rejected"
            status = "executed"
            error = ""
        prediction = CasePrediction(
            case_id=task.case_id,
            strategy=strategy,
            round=round_number,
            delivered_job_keys=list(keys),
            saved_job_keys=list(keys),
            terminal_status=terminal_status,
            terminal_reason=terminal_reason,
            planner_calls=budget.calls,
            consumed_steps=steps,
            elapsed_ms=(perf_counter() - started) * 1000,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            usage_complete=complete,
            unauthorized_writes=unauthorized,
            duplicate_writes=duplicates,
            constraint_violations=violations,
            error=error,
            status=status,
        )
        evidence = {
            "reader_fault_events": reader_fault_events,
            "reader_counts": {name: counts[name] for name in ("accepted", "rejected", "review", "error")},
            "reader_routes": route_reasons,
            "reader_pages": page_records,
            "duplicate_delivery_suppressed": duplicate_delivery_suppressed,
            "provider_records": records,
            "provider_failures": [item for item in records if item.get("status") == "error"],
            "db_job_count": len(jobs),
            "sqlite": db_path.relative_to(episode_dir.parent.parent).as_posix(),
        }
        return prediction, evidence

    def _episode_dir(self, case_id: str, strategy: str, round_number: int) -> Path:
        assert self.output is not None
        digest = hashlib.sha256(f"{case_id}\0{strategy}\0{round_number}".encode()).hexdigest()[:12]
        directory = self.output / "episodes" / f"{self.config.suite}-{digest}-{uuid4().hex[:8]}"
        directory.mkdir(parents=True, exist_ok=False)
        return directory

    def _append_result(self, prediction: CasePrediction) -> None:
        assert self.output is not None
        path = self.output / "results.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(prediction.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _summary(self, predictions: Sequence[CasePrediction]) -> dict[str, Any]:
        summary = summarize_results(self.tasks, predictions, planned_repeats=self.config.runs)
        observed = {(item.case_id, item.strategy, item.round) for item in predictions}
        planned = planned_episode_order(
            self.tasks, self.config.strategies, runs=self.config.runs,
            seed=self.config.seed, suite=self.config.suite,
        )
        missing = [
            {"case_id": case_id, "strategy": strategy, "round": round_number}
            for case_id, strategy, round_number in planned
            if (case_id, strategy, round_number) not in observed
        ]
        failed = [
            {"case_id": item.case_id, "strategy": item.strategy, "round": item.round}
            for item in predictions
            if item.status == "error" or bool(item.error)
        ]
        summary["selected_strategies"] = list(self.config.strategies)
        summary["missing_planned_episodes"] = missing
        summary["failed_planned_episodes"] = failed
        summary["incomplete_planned_episodes"] = missing + failed
        summary["planned_episode_keys"] = [
            {"case_id": case_id, "strategy": strategy, "round": round_number}
            for case_id, strategy, round_number in planned
        ]
        details = {**self._existing_details, **self._details}
        evidence_by_strategy: dict[str, dict[str, Any]] = {}
        page_metrics_by_strategy: dict[str, dict[str, Any]] = {}
        for strategy in self.config.strategies:
            values = [item for item in details.values() if item.get("strategy") == strategy]
            planner_totals = {
                key: sum(
                    int(item.get("planner_metrics", {}).get(key, 0))
                    for item in values
                )
                for key in (
                    "planner_opportunities",
                    "planner_calls",
                    "policy_skips",
                    "budget_skips",
                    "fallback_decisions",
                    "invalid_actions",
                )
            }
            planner_totals["approval_wait_ms"] = sum(
                float(item.get("planner_metrics", {}).get("approval_wait_ms", 0))
                for item in values
            )
            reader_totals = {
                key: sum(
                    int(item.get("reader_counts", {}).get(key, 0))
                    for item in values
                )
                for key in ("accepted", "rejected", "review", "error")
            }
            page_metrics = summarize_reader_page_metrics(
                list(details.values()), strategy
            )
            page_metrics_by_strategy[strategy] = page_metrics
            evidence_by_strategy[strategy] = {
                "planner": planner_totals,
                "reader": reader_totals,
                "reader_pages": page_metrics,
            }
        summary["episode_evidence"] = evidence_by_strategy
        for strategy, block in summary["strategies"].items():
            if strategy not in page_metrics_by_strategy:
                continue
            page_metrics = page_metrics_by_strategy[strategy]
            block["task_review_rate"] = block["review_rate"]
            block["reader_pages"] = page_metrics
            block["page_review_rate"] = page_metrics["review_rate"]
            block["page_error_rate"] = page_metrics["error_rate"]
        summary["budget"] = self.budget.snapshot()
        bootstraps: dict[str, Any] = {}
        selected = list(self.config.strategies)
        for index, left in enumerate(selected):
            for right in selected[index + 1:]:
                try:
                    bootstraps[f"{left}_minus_{right}"] = paired_group_bootstrap(
                        self.tasks,
                        predictions,
                        left=left,
                        right=right,
                        seed=self.config.seed,
                        iterations=2000,
                        planned_repeats=self.config.runs,
                    )
                except Exception as error:
                    bootstraps[f"{left}_minus_{right}"] = {
                        "error": type(error).__name__, "iterations": 2000,
                    }
        summary["paired_bootstrap"] = bootstraps
        return summary

    def _write_artifacts(self, predictions: Sequence[CasePrediction]) -> None:
        assert self.output is not None
        summary = self._summary(predictions)
        _write_json(self.output / "summary.json", summary)
        (self.output / "report.md").write_text(self._render_report(summary), encoding="utf-8")
        (self.output / "failures.md").write_text(self._render_failures(predictions, summary), encoding="utf-8")
        previous = self._existing_details
        details = {**previous, **self._details}
        manifest = {
            "schema_version": 1,
            "run_id": self._run_id,
            "created_at": self._created_at,
            "updated_at": datetime.now(UTC).isoformat(),
            "suite": self.config.suite,
            "config": self.config.fingerprint_payload(),
            "frozen_config_sha256": self.config.fingerprint,
            "dataset_manifest": _json_value(self.manifest),
            "dataset_manifest_sha256": _sha256_json(_json_value(self.manifest)),
            "code_version": {
                "dataset_code_version_match": self.manifest.get(
                    "source_code_reproducibility", {}
                ).get("current_match"),
                "declared_dataset_source_code_hashes": self.manifest.get(
                    "source_code_hashes", {}
                ),
                "observed_source_code_hashes_at_run": current_source_code_hashes(),
                "semantics": "observed code bytes at run time; not a live-environment proof",
            },
            "planned_episodes": summary["planned_episode_keys"],
            "missing_planned_episodes": summary["missing_planned_episodes"],
            "budget": self.budget.snapshot(),
            "halted_reason": self.halted_reason,
            "resumed_keys": self.resumed_keys,
            "episode_details": details,
        }
        _write_json(self.output / "manifest.json", manifest)
        self._run_id = manifest["run_id"]
        self._created_at = manifest["created_at"]

    def _render_report(self, summary: Mapping[str, Any]) -> str:
        lines = [
            "# Model-value benchmark",
            "",
            f"- Suite: `{self.config.suite}`",
            f"- Split: `{self.config.split}`",
            f"- Strategies: `{', '.join(self.config.strategies)}`",
            f"- Repeats: {self.config.runs}",
            f"- Seed: {self.config.seed}",
            f"- Provider/model: `{self.config.provider or 'none'} / {self.config.model or 'none'}`",
            f"- Exploratory run: {self.config.exploratory}",
            f"- Request cap: {self.config.effective_max_calls}",
            f"- Observed-token soft cap: {self.config.max_observed_tokens}",
            f"- Halted reason: {self.halted_reason or 'none'}",
            "",
            (
                "Missing or failed episodes remain missing; they are not successes. "
                "Token usage is not a bill and may exclude requests whose usage did not return."
            ),
            "",
            (
                "Exploratory mode does not satisfy the formal human-reviewed-label gate."
                if self.config.exploratory
                else "Formal label policy was delegated to the frozen dataset loader."
            ),
            "",
            "## Strategy results",
            "",
        ]
        for strategy in self.config.strategies:
            block = summary["strategies"][strategy]
            evidence = summary["episode_evidence"][strategy]
            lines.extend(
                [
                    f"### {strategy}",
                    "",
                    (
                        f"- Planned: {block['planned_episodes']}; "
                        f"observed: {block['observed']}; missing: {block['missing']}"
                    ),
                    (
                        f"- Target completion: {block['target_completion']['numerator']}/"
                        f"{block['target_completion']['denominator']} = "
                        f"{block['target_completion']['rate']}"
                    ),
                    (
                        f"- Correct/incorrect/missed: {block['correct_delivered']}/"
                        f"{block['incorrect_delivered']}/{block['missed']}"
                    ),
                    f"- Precision/coverage: {block['precision']} / {block['coverage']}",
                    f"- Review/error rate: {block['review_rate']['rate']} / {block['error_rate']['rate']}",
                    (
                        f"- Model calls: {block['model_calls']}; "
                        f"observed tokens: {block['observed_tokens']}; "
                        f"usage missing: {block['usage_missing']}"
                    ),
                    f"- Elapsed P50/P95 ms: {block['elapsed_p50']} / {block['elapsed_p95']}",
                ]
            )
            if self.config.suite == "planner":
                planner = evidence["planner"]
                lines.extend(
                    [
                        (
                            f"- Planner opportunities/skips/fallbacks: "
                            f"{planner['planner_opportunities']}/"
                            f"{planner['policy_skips']}/{planner['fallback_decisions']}"
                        ),
                        f"- Approval wait total ms: {planner['approval_wait_ms']}",
                    ]
                )
            else:
                reader = evidence["reader"]
                lines.append(
                    f"- Reader accepted/rejected/review/error: "
                    f"{reader['accepted']}/{reader['rejected']}/"
                    f"{reader['review']}/{reader['error']}"
                )
                pages = evidence["reader_pages"]
                lines.append(
                    f"- Reader page accepted/rejected/review/error: "
                    f"{pages['counts']['accepted']}/{pages['counts']['rejected']}/"
                    f"{pages['counts']['review']}/{pages['counts']['error']}; "
                    f"page review/error rate: {pages['review_rate']['rate']} / "
                    f"{pages['error_rate']['rate']}"
                )
            lines.append("")
        lines.extend(
            [
                "## Paired group bootstrap",
                "",
                (
                    "Intervals resample independent `group_id` units 2,000 times with the "
                    "frozen seed. Repeats are averaged inside each group."
                ),
                "",
            ]
        )
        for name, block in summary.get("paired_bootstrap", {}).items():
            if "error" in block:
                lines.append(f"- `{name}`: unavailable ({block['error']})")
            else:
                lines.append(
                    f"- `{name}`: mean {block['mean_difference']}, 95% CI {block['ci95']}, groups {block['groups']}"
                )
        lines.extend(
            [
                "",
                (
                    "This report describes recorded benchmark episodes. It does not claim "
                    "human-reviewed labels or billing-verified savings unless the dataset "
                    "manifest and captured run independently establish them."
                ),
            ]
        )
        return "\n".join(lines) + "\n"

    def _render_failures(self, predictions: Sequence[CasePrediction], summary: Mapping[str, Any]) -> str:
        lines = ["# Failures and missing episodes", ""]
        errors = [item for item in predictions if item.status == "error" or item.error]
        for item in errors:
            lines.append(
                f"- `{item.case_id}/{item.strategy}/{item.round}` failed: {item.error or item.terminal_reason}"
            )
        for item in summary["missing_planned_episodes"]:
            lines.append(
                f"- `{item['case_id']}/{item['strategy']}/{item['round']}` was not run (missing, not success)"
            )
        details = {**self._existing_details, **self._details}
        for key, detail in sorted(details.items()):
            for failure in detail.get("provider_failures", []):
                lines.append(
                    f"- `{key}` provider failure: "
                    f"{failure.get('error_type', 'unknown')}"
                    f" (request {failure.get('request_id', 'unknown')})"
                )
        if len(lines) == 2:
            lines.append("No recorded failures or missing episodes.")
        return "\n".join(lines) + "\n"

    def _result_payload(self, predictions: Sequence[CasePrediction]) -> dict[str, Any]:
        return {
            "mode": "live",
            "suite": self.config.suite,
            "output_dir": str(self.output),
            "summary": self._summary(predictions),
            "episode_details": list(self._details.values()),
            "resumed_keys": self.resumed_keys,
            "halted_reason": self.halted_reason,
        }

def _known_removed_page(page: BrowserPage) -> bool:
    availability = str(page.metadata.get("availability", "")).strip().casefold()
    if availability == "removed":
        return True
    for line in page.content.splitlines():
        normalized = " ".join(line.strip().casefold().split())
        if not normalized.startswith("status:"):
            continue
        if any(
            marker in normalized
            for marker in (
                "removed",
                "no longer available",
                "not found",
                "已下线",
                "已失效",
            )
        ):
            return True
    return False


def _reader_evidence_reason(reasons: Sequence[str]) -> str | None:
    normalized = [reason.strip().casefold() for reason in reasons if reason.strip()]
    if "location_conflict" in normalized:
        return "location_conflict"
    if "location_unknown" in normalized:
        return "location_unknown"
    if any(
        reason in {
            "insufficient",
            "insufficient_text",
            "model_reported_uncertainty",
            "garbled_text",
            "possibly_truncated_text",
            "text_integrity",
        }
        for reason in normalized
    ):
        return "insufficient_text"
    if any(reason in {"recruitment_notice", "job_list"} for reason in normalized):
        return "needs_human_review"
    if any(
        reason.startswith(("missing_", "unsupported_"))
        or "ambiguous" in reason
        or reason == "reader_review"
        for reason in normalized
    ):
        return "reader_review"
    return None


def _reader_terminal_reason(
    task: ModelValueTask,
    counts: Counter[str],
    reasons: Sequence[str],
    *,
    attempted: int,
    candidate_count: int,
    delivered: int,
) -> str:
    if "deadline_exceeded" in reasons:
        return "deadline_exceeded"
    if counts["error"]:
        return next(
            (
                reason
                for reason in reasons
                if any(
                    marker in reason.casefold()
                    for marker in ("error", "timeout", "exceeded")
                )
            ),
            "reader_error",
        )
    evidence_reason = _reader_evidence_reason(reasons)
    if evidence_reason is not None:
        return evidence_reason
    if delivered >= task.target_count:
        return "target_reached"
    if attempted >= candidate_count:
        if delivered:
            return "candidate_shortage"
        return "candidate_exhausted" if candidate_count else "no_candidates"
    if counts["review"]:
        return "reader_review"
    if counts["accepted"]:
        return "reader_accepted"
    return reasons[0] if reasons else "reader_rejected"


def _load_results(path: Path) -> list[CasePrediction]:
    if not path.is_file():
        return []
    predictions: list[CasePrediction] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            predictions.append(CasePrediction.model_validate(json.loads(line)))
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid results.jsonl line {line_number}") from error
    seen: set[tuple[str, str, int]] = set()
    for prediction in predictions:
        key = (prediction.case_id, prediction.strategy, prediction.round)
        if key in seen:
            raise ValueError(f"duplicate unique episode key: {key}")
        seen.add(key)
    return predictions


def _prior_budget(snapshot: Mapping[str, Any]) -> dict[str, int]:
    known_tokens = int(
        snapshot.get(
            "known_tokens",
            snapshot.get("observed_tokens") or 0,
        )
    )
    spent_tokens = int(snapshot.get("spent_token_estimate", known_tokens))
    return {
        "prior_calls": int(snapshot.get("calls", 0)),
        "prior_spent_tokens": max(spent_tokens, known_tokens),
        "prior_known_tokens": min(known_tokens, max(spent_tokens, known_tokens)),
        "prior_usage_missing": int(snapshot.get("usage_missing", 0)),
    }


def _budget_state_from_journal(path: Path) -> dict[str, int] | None:
    """Reconcile durable admissions, treating unsettled requests conservatively."""

    if not path.is_file():
        return None
    admitted: dict[str, int] = {}
    settled: dict[str, int | None] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid budget journal line {line_number}") from error
        if not isinstance(event, Mapping):
            raise ValueError(f"invalid budget journal line {line_number}")
        reservation = event.get("reservation")
        if not isinstance(reservation, str) or not reservation:
            raise ValueError(f"budget journal line {line_number} lacks a reservation id")
        if event.get("event") == "admit":
            estimate = event.get("estimated_tokens")
            if not isinstance(estimate, int) or isinstance(estimate, bool) or estimate < 0:
                raise ValueError(f"budget journal line {line_number} has an invalid estimate")
            if reservation in admitted:
                raise ValueError(f"budget journal repeats admission {reservation}")
            admitted[reservation] = estimate
        elif event.get("event") == "settle":
            if reservation not in admitted:
                raise ValueError(f"budget journal settles unknown admission {reservation}")
            if reservation in settled:
                raise ValueError(f"budget journal repeats settlement {reservation}")
            total = event.get("total_tokens")
            if total is not None and (
                not isinstance(total, int) or isinstance(total, bool) or total < 0
            ):
                raise ValueError(f"budget journal line {line_number} has invalid tokens")
            settled[reservation] = total
        else:
            raise ValueError(f"budget journal line {line_number} has an unknown event")
    if not admitted:
        return None
    known = 0
    unknown = 0
    for reservation, estimate in admitted.items():
        total = settled.get(reservation)
        if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
            known += total
        else:
            unknown += estimate
    return {
        "prior_calls": len(admitted),
        "prior_spent_tokens": known + unknown,
        "prior_known_tokens": known,
        "prior_usage_missing": len(admitted) - sum(
            1 for value in settled.values() if isinstance(value, int)
        ),
    }


def _prepare_output(
    config: BenchmarkRunConfig,
) -> tuple[Path, Sequence[CasePrediction], Mapping[str, Any]]:
    assert config.output_dir is not None
    output = config.output_dir
    if output.exists() and not config.resume:
        raise FileExistsError(f"output directory exists; pass --resume to continue: {output}")
    if config.resume:
        manifest_path = output / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError("cannot resume without manifest.json")
        manifest = _read_json(manifest_path)
        if not isinstance(manifest, Mapping):
            raise ValueError("manifest.json must be an object")
        if manifest.get("frozen_config_sha256") != config.fingerprint:
            raise ValueError("resume configuration does not match the frozen run configuration")
        predictions = _load_results(output / "results.jsonl")
        return output, predictions, manifest
    output.mkdir(parents=True, exist_ok=False)
    (output / "results.jsonl").write_text("", encoding="utf-8")
    (output / "budget-journal.jsonl").write_text("", encoding="utf-8")
    return output, [], {}


def _default_dataset_loader():
    return load_model_value_dataset


def _default_planner_factory(
    *, provider: str, model: str, budget: ScopedModelCallBudget, timeout_seconds: int
):
    from web_task_agent.agent_planner import build_configured_agent_planner

    return build_configured_agent_planner(
        provider=provider, model=model, budget=budget, timeout_seconds=timeout_seconds
    )


def _default_reader_factory(
    *, provider: str, model: str, budget: ScopedModelCallBudget, timeout_seconds: int,
    provider_config: LlmProviderConfig | None = None,
):
    config = provider_config or build_llm_provider_config(provider=provider, model=model)
    reader = EvidenceJobReader(config, timeout_seconds=timeout_seconds)
    recorder = RecordingLlmTransport(reader.transport, provider=config.provider, model=config.model)
    reader.transport = recorder
    reader.telemetry = recorder
    return reader


def _estimated_requests(
    tasks: Sequence[ModelValueTask],
    catalogs: Mapping[str, _CatalogView],
    config: BenchmarkRunConfig,
) -> int:
    total = 0
    for task in tasks:
        for strategy in config.strategies:
            if strategy == "rules":
                continue
            page_bound = config.max_planner_calls_per_case if config.suite == "planner" else min(
                config.max_planner_calls_per_case,
                max(1, len(catalogs[task.case_id].candidate_urls)),
            )
            total += config.runs * page_bound
    return total


async def run_benchmark(
    config: BenchmarkRunConfig,
    *,
    dataset_loader: DatasetLoader | None = None,
    planner_factory: PlannerFactory | None = None,
    reader_factory: ReaderFactory | None = None,
    page_resolver: PageResolver | None = None,
) -> dict[str, Any]:
    """Validate, dry-run, or execute one suite with shared budgets."""

    loader = dataset_loader or _default_dataset_loader()
    require_review = config.split == "test"
    loaded_tasks, catalog_raw, manifest = loader(
        config.dataset, split=None, require_human_review=require_review
    )
    all_tasks = [
        item if isinstance(item, ModelValueTask) else ModelValueTask.model_validate(item)
        for item in loaded_tasks
    ]
    validation = validate_dataset(all_tasks, manifest)
    raw_catalogs = {str(key): dict(value) for key, value in catalog_raw.items()}
    catalogs = {
        task.case_id: _catalog_view(task, raw_catalogs[task.case_id])
        for task in all_tasks
        if task.case_id in raw_catalogs
    }
    tasks = [task for task in all_tasks if task.split == config.split]
    if config.limit_cases is not None:
        tasks = tasks[: config.limit_cases]
    if not tasks:
        raise ValueError(f"no {config.split!r} tasks were selected")
    missing_catalogs = [task.case_id for task in tasks if task.case_id not in catalogs]
    if missing_catalogs:
        raise ValueError(f"catalog entries are missing for cases: {missing_catalogs}")
    estimated = _estimated_requests(tasks, catalogs, config)
    base = {
        "suite": config.suite,
        "split": config.split,
        "strategies": list(config.strategies),
        "planned_episodes": len(tasks) * config.runs * len(config.strategies),
        "estimated_max_requests": estimated,
        "request_cap": config.effective_max_calls,
        "observed_token_soft_cap": config.max_observed_tokens,
        "validation": validation,
        "frozen_config_sha256": config.fingerprint,
        "exploratory": config.exploratory,
    }
    if config.validate_only:
        return {**base, "mode": "validate_only"}
    if config.dry_run:
        return {**base, "mode": "dry_run"}

    model_strategies = [strategy for strategy in config.strategies if strategy != "rules"]
    needs_default_planner = config.suite == "planner" and any(
        strategy != "rules" for strategy in config.strategies
    ) and planner_factory is None
    needs_default_reader = config.suite == "reader" and any(
        strategy != "rules" for strategy in config.strategies
    ) and reader_factory is None
    if model_strategies and (needs_default_planner or needs_default_reader) and (
        not config.provider or not config.model
    ):
        raise ValueError("--provider and an explicit --model are required for live model strategies")
    provider_config = None
    if needs_default_planner or needs_default_reader:
        provider_config = build_llm_provider_config(
            provider=config.provider or "", model=config.model
        )
    if planner_factory is None:
        planner_factory = _default_planner_factory
    if reader_factory is None:
        def default_reader_factory(**kwargs):
            return _default_reader_factory(**kwargs, provider_config=provider_config)

        reader_factory = default_reader_factory

    manifest_view = _manifest_payload(manifest)
    output, existing, previous_manifest = _prepare_output(config)
    if previous_manifest and previous_manifest.get("dataset_manifest_sha256") != _sha256_json(
        _json_value(manifest_view)
    ):
        raise ValueError("resume dataset manifest does not match the frozen run manifest")
    resolver = page_resolver or ArchivePageResolver(_dataset_root_from_manifest(config.dataset, manifest_view))
    runner = _Runner(
        config,
        tasks,
        catalogs,
        raw_catalogs,
        manifest_view,
        resolver,
        planner_factory=planner_factory,
        reader_factory=reader_factory,
    )
    journal_path = output / "budget-journal.jsonl"
    journal_budget = _budget_state_from_journal(journal_path)
    if previous_manifest or journal_budget is not None:
        runner.prior_budget = journal_budget or _prior_budget(previous_manifest.get("budget", {}))
        runner._existing_details = dict(previous_manifest.get("episode_details", {}))
        runner._run_id = str(previous_manifest.get("run_id", ""))
        runner._created_at = str(previous_manifest.get("created_at", datetime.now(UTC).isoformat()))
        runner.budget = RunModelCallBudget(
            max_calls=config.effective_max_calls,
            max_observed_tokens=config.max_observed_tokens,
            deadline_seconds=max(3600, config.case_deadline_seconds * len(tasks) * max(1, config.runs)),
            journal_path=journal_path,
            **runner.prior_budget,
        )
    else:
        runner.budget = RunModelCallBudget(
            max_calls=config.effective_max_calls,
            max_observed_tokens=config.max_observed_tokens,
            deadline_seconds=max(3600, config.case_deadline_seconds * len(tasks) * max(1, config.runs)),
            journal_path=journal_path,
        )
    return await runner.execute(existing=existing)


def _parse_strategies(value: str) -> tuple[str, ...]:
    parts = tuple(item.strip() for item in value.split(",") if item.strip())
    return BenchmarkRunConfig._validate_strategies(parts)


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the C5 model-value benchmark")
    parser.add_argument("--suite", required=True, choices=("planner", "reader"))
    parser.add_argument("--strategies", type=_parse_strategies, default=_DEFAULT_STRATEGIES,
                        metavar="rules,always,adaptive")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--split", required=True, choices=("dev", "test"))
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261023)
    parser.add_argument("--limit-cases", type=int)
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--max-planner-calls-per-case", type=int, default=4)
    parser.add_argument("--max-observed-tokens", type=int, default=2_000_000)
    parser.add_argument("--case-deadline-seconds", type=float, default=60)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--exploratory", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_cli_parser()
    args = parser.parse_args(argv)
    try:
        config = BenchmarkRunConfig(
            suite=args.suite,
            strategies=args.strategies,
            dataset=args.dataset,
            split=args.split,
            provider=args.provider,
            model=args.model,
            runs=args.runs,
            seed=args.seed,
            limit_cases=args.limit_cases,
            max_calls=args.max_calls,
            max_planner_calls_per_case=args.max_planner_calls_per_case,
            max_observed_tokens=args.max_observed_tokens,
            case_deadline_seconds=args.case_deadline_seconds,
            dry_run=args.dry_run,
            validate_only=args.validate_only,
            output_dir=args.output_dir,
            resume=args.resume,
            exploratory=args.exploratory,
        )
        result = asyncio.run(run_benchmark(config))
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(_json_value(result), ensure_ascii=False, sort_keys=True))
    return 0
