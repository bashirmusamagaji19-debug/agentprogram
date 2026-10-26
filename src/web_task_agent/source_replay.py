"""Deterministic, read-only replay from a verified source archive."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from web_task_agent.browser import PageEmptyError, PageHttpError, PageTimeoutError
from web_task_agent.models import BrowserPage
from web_task_agent.page_reliability import PageFailureCategory, PageRemovedError
from web_task_agent.source_archive import SourceArchive
from web_task_agent.source_provenance import (
    CandidateEvidence,
    ResolutionAttempt,
    normalize_conditions,
    validate_public_url,
    validate_sha256,
)


class SourceReplayError(RuntimeError):
    code = "source_replay_error"


class MissingSnapshotError(SourceReplayError):
    code = "missing_snapshot"

    def __init__(self, url: str):
        super().__init__(f"missing_snapshot: no archived content URL {url}")
        self.url = url


class MissingModelRecordError(SourceReplayError):
    code = "missing_model_record"

    def __init__(self) -> None:
        super().__init__("missing_model_record: no exact archived model response")


class ConditionsChangedError(SourceReplayError):
    """The requested replay conditions differ from the archived run."""

    code = "conditions_changed"

    def __init__(
        self,
        archived_conditions: Mapping[str, Any],
        requested_conditions: Mapping[str, Any],
    ):
        super().__init__("conditions_changed: replay conditions differ from the archive")
        self.archived_conditions = copy.deepcopy(dict(archived_conditions))
        self.requested_conditions = copy.deepcopy(dict(requested_conditions))


class ReplayedFailure(SourceReplayError):
    """A safe, synthetic failure for an unknown archived failure category."""

    def __init__(self, category: PageFailureCategory):
        super().__init__(f"archived failure: {category.value}")
        self.category = category


def reader_input_sha256(page: BrowserPage) -> str:
    """Hash the exact model-facing page input, without normalization."""

    payload = {"url": page.url, "title": page.title, "content": page.content}
    material = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def prompt_sha256(prompt: str) -> str:
    if not isinstance(prompt, str):
        raise TypeError("prompt must be text")
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def parameters_sha256(parameters: Mapping[str, Any] | None = None) -> str:
    """Hash canonical model parameters; omitted parameters mean the empty default."""

    if parameters is None:
        value: dict[str, Any] = {}
    elif isinstance(parameters, Mapping):
        value = copy.deepcopy(dict(parameters))
    else:
        raise ValueError("model parameters must be a mapping")
    try:
        material = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("model parameters must be canonical JSON data") from exc
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class RecordedEvidenceReader:
    """Look up a cached model result by exact input, prompt, provider and model.

    This class is deliberately synchronous and has no transport or fallback
    reader. Parameters participate in the identity; legacy records without a
    ``parameters`` field match only the empty default. A missing identity is an
    error; it never attempts a live call.
    """

    def __init__(
        self,
        records: Iterable[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]] | str | Path,
        *,
        provider: str,
        model: str,
        prompt: str,
        parameters: Mapping[str, Any] | None = None,
    ):
        if (
            not isinstance(provider, str)
            or not provider.strip()
            or not isinstance(model, str)
            or not model.strip()
            or not isinstance(prompt, str)
        ):
            raise ValueError("recorded reader requires exact provider, model and prompt")
        self.provider = provider
        self.model = model
        self.prompt = prompt
        self._prompt_hash = prompt_sha256(prompt)
        self._parameters_hash = parameters_sha256(parameters)
        self.parameters = copy.deepcopy(dict(parameters)) if parameters is not None else {}
        self._records: dict[tuple[str, str, str, str, str], Mapping[str, Any]] = {}
        for record in self._load_records(records):
            if not isinstance(record, Mapping):
                raise ValueError("recorded model entries must be objects")
            try:
                input_hash = record["input_sha256"]
                prompt_hash = record["prompt_sha256"]
                provider = record["provider"]
                model = record["model"]
                result = record.get("result", record.get("response"))
            except KeyError as exc:
                raise ValueError("recorded model entry is missing identity or result") from exc
            if result is None:
                raise ValueError("recorded model entry is missing result")
            if not all(isinstance(value, str) for value in (provider, model)):
                raise ValueError("recorded provider and model must be exact strings")
            if not provider.strip() or not model.strip():
                raise ValueError("recorded provider and model must not be blank")
            raw_parameters = record.get("parameters")
            recorded_parameters_hash = record.get("parameters_sha256")
            if raw_parameters is not None:
                parameter_hash = parameters_sha256(raw_parameters)
                if recorded_parameters_hash is not None:
                    validate_sha256(recorded_parameters_hash, "recorded parameters_sha256")
                    if parameter_hash != recorded_parameters_hash:
                        raise ValueError("recorded parameters do not match parameters_sha256")
            elif recorded_parameters_hash is not None:
                parameter_hash = validate_sha256(recorded_parameters_hash, "recorded parameters_sha256")
            else:
                parameter_hash = parameters_sha256()
            identity = (
                validate_sha256(input_hash, "recorded input_sha256"),
                validate_sha256(prompt_hash, "recorded prompt_sha256"),
                provider,
                model,
                parameter_hash,
            )
            if not isinstance(result, Mapping):
                raise ValueError("recorded model result must be an object")
            existing = self._records.get(identity)
            if existing is not None and existing != result:
                raise ValueError("recorded model identity has conflicting results")
            self._records[identity] = result

    def read(self, page: BrowserPage) -> dict[str, Any]:
        identity = (
            reader_input_sha256(page),
            self._prompt_hash,
            self.provider,
            self.model,
            self._parameters_hash,
        )
        result = self._records.get(identity)
        if result is None:
            raise MissingModelRecordError()
        return copy.deepcopy(dict(result))

    @staticmethod
    def _load_records(
        records: Iterable[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]] | str | Path,
    ) -> Iterable[Mapping[str, Any]]:
        if isinstance(records, (str, Path)):
            path = Path(records)
            if not path.is_file():
                raise FileNotFoundError(path)
            raw = path.read_text(encoding="utf-8")
            if path.suffix.casefold() == ".jsonl":
                return [json.loads(line) for line in raw.splitlines() if line.strip()]
            payload = json.loads(raw)
            if isinstance(payload, Mapping):
                payload = payload.get("records")
            if not isinstance(payload, list):
                raise ValueError("recorded model JSON must contain a records list")
            return payload
        if isinstance(records, Mapping):
            if isinstance(records.get("records"), list):
                return records["records"]
            keyed: list[Mapping[str, Any]] = []
            for input_hash, record in records.items():
                if not isinstance(record, Mapping):
                    raise ValueError("keyed recorded model entries must be objects")
                item = dict(record)
                item.setdefault("input_sha256", input_hash)
                keyed.append(item)
            return keyed
        return records


RecordedReader = RecordedEvidenceReader


class ArchiveBrowser:
    """A BrowserClient-compatible replay over one immutable run archive.

    Pass current request conditions through ``conditions``. A mismatch raises
    ``ConditionsChangedError`` unless the caller explicitly selects
    ``conditions_mismatch="use_archived"``. Returned pages expose both the
    archived and requested conditions and the match result.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        mode: Literal["content", "attempts"] = "content",
        recorded_reader: RecordedEvidenceReader | None = None,
        reader: RecordedEvidenceReader | None = None,
        conditions: Mapping[str, str | int | list[str]] | None = None,
        conditions_mismatch: Literal["error", "use_archived"] = "error",
    ):
        if mode not in {"content", "attempts"}:
            raise ValueError("mode must be content or attempts")
        if conditions_mismatch not in {"error", "use_archived"}:
            raise ValueError("conditions_mismatch must be error or use_archived")
        if recorded_reader is not None and reader is not None and recorded_reader is not reader:
            raise ValueError("pass only one recorded reader")
        self.archive = SourceArchive(root)
        self.mode = mode
        self.recorded_reader = recorded_reader or reader
        self.conditions = normalize_conditions(conditions) if conditions is not None else None
        self.conditions_mismatch = conditions_mismatch
        self._attempt_positions: dict[str, int] = {}
        self._attempt_lock = threading.Lock()

    async def search(self, query: str, target_count: int) -> list[BrowserPage]:
        if target_count <= 0:
            return []
        self.archive.verify()
        manifest = self.archive.read_manifest()
        condition_metadata = self._condition_metadata(manifest)
        candidates = self.archive.read_candidates()
        selected: dict[str, CandidateEvidence] = {}
        for candidate in candidates:
            if candidate.query_context.get("type") == "search_page":
                continue
            if not _candidate_matches(candidate, query):
                continue
            # Candidate history may contain the same native job under multiple
            # city conditions. Keep the latest matching public context.
            selected[candidate.candidate_id] = candidate

        pages = []
        for candidate in list(selected.values())[:target_count]:
            try:
                page = self._latest_for_candidate(candidate.candidate_id, manifest.snapshot_ids)
            except MissingSnapshotError:
                # Discovery is evidence of a URL, not evidence of its job body.
                # Keep it available to search callers without fabricating a
                # snapshot or invoking any transport when they later open it.
                page = BrowserPage(url=candidate.requested_url, title=candidate.title,
                    content="", source="archive-discovery", metadata={
                        "candidate_id": candidate.candidate_id,
                        "candidate_urls": [candidate.requested_url],
                    })
            metadata = dict(page.metadata)
            metadata.update(
                {
                    "keyword": candidate.keyword,
                    "location": candidate.location,
                    "source_spec": candidate.source_spec,
                    "source_job_id": candidate.source_job_id,
                    "discovery_url": candidate.discovery_url,
                    "query_context": dict(candidate.query_context),
                    **condition_metadata,
                }
            )
            pages.append(page.model_copy(update={"metadata": metadata}))
        return pages

    async def open_url(self, url: str) -> BrowserPage:
        normalized = validate_public_url(url, "url")
        self.archive.verify()
        manifest = self.archive.read_manifest()
        condition_metadata = self._condition_metadata(manifest)
        candidates = [
            candidate
            for candidate in self.archive.read_candidates()
            if normalized in {candidate.requested_url, candidate.canonical_url}
            and candidate.query_context.get("type") != "search_page"
        ]
        if not candidates:
            raise MissingSnapshotError(normalized)

        attempts = [
            attempt
            for attempt in self.archive.read_attempts()
            if attempt.candidate_id in {candidate.candidate_id for candidate in candidates}
            and attempt.operation_kind != "search"
        ]
        page = None
        if self.mode == "attempts" and attempts:
            page = self._next_open_result(normalized, attempts)

        if page is None:
            page = self._latest_for_candidate(candidates[0].candidate_id, manifest.snapshot_ids)
        metadata = dict(page.metadata)
        metadata["candidate_ids"] = list(dict.fromkeys(item.candidate_id for item in candidates))
        metadata.update(condition_metadata)
        return page.model_copy(update={"metadata": metadata})

    def read_recorded(self, page: BrowserPage) -> dict[str, Any]:
        if self.recorded_reader is None:
            raise MissingModelRecordError()
        return self.recorded_reader.read(page)

    def _latest_for_candidate(self, candidate_id: str, snapshot_ids: list[str]) -> BrowserPage:
        candidates: list[tuple[str, int, BrowserPage]] = []
        for index, snapshot_id in enumerate(snapshot_ids):
            page = self.archive.read_page(snapshot_id)
            if page.metadata.get("candidate_id") == candidate_id:
                candidates.append((str(page.metadata.get("captured_at", "")), index, page))
        if not candidates:
            raise MissingSnapshotError(f"candidate:{candidate_id}")
        return max(candidates, key=lambda item: (item[0], item[1]))[2]

    def _condition_metadata(self, manifest: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "archived_conditions": copy.deepcopy(dict(manifest.conditions)),
        }
        if self.conditions is None:
            return metadata
        matched = self.conditions == manifest.conditions
        metadata.update(
            {
                "requested_conditions": copy.deepcopy(dict(self.conditions)),
                "conditions_match": matched,
            }
        )
        if not matched and self.conditions_mismatch == "error":
            raise ConditionsChangedError(manifest.conditions, self.conditions)
        return metadata

    def _next_open_result(self, url: str, attempts: list[ResolutionAttempt]) -> BrowserPage | None:
        """Consume one public operation, preserving legacy ungrouped failures.

        Without operation_id the historic contract injects each failed layer
        separately, then falls back to the latest body. Grouped records instead
        reproduce the terminal outcome of one original public open call.
        """

        groups: list[list[ResolutionAttempt]] = []
        identified: dict[str, list[ResolutionAttempt]] = {}
        for attempt in attempts:
            if attempt.operation_id is None:
                if attempt.status.casefold() in {"failure", "failed", "error"}:
                    groups.append([attempt])
            else:
                if attempt.operation_id not in identified:
                    identified[attempt.operation_id] = []
                    groups.append(identified[attempt.operation_id])
                identified[attempt.operation_id].append(attempt)
        with self._attempt_lock:
            position = self._attempt_positions.get(url, 0)
            if position >= len(groups):
                return None
            self._attempt_positions[url] = position + 1
            group = groups[position]
        terminal = group[-1]
        if terminal.status.casefold() in {"failure", "failed", "error"}:
            raise _safe_failure(terminal)
        if terminal.status.casefold() in {"success", "succeeded", "ok"} and terminal.snapshot_id is not None:
            return self.archive.read_page(terminal.snapshot_id)
        raise MissingSnapshotError(f"operation:{terminal.operation_id}")


def _candidate_matches(candidate: CandidateEvidence, query: str) -> bool:
    normalized = re.sub(r"\s+", " ", query).strip().casefold()
    if not normalized:
        return False
    keyword = re.sub(r"\s+", " ", candidate.keyword).strip().casefold()
    keyword_tokens = re.findall(r"[\w+#.-]+", keyword)
    if (
        keyword
        and keyword not in normalized
        and (not keyword_tokens or not all(token in normalized for token in keyword_tokens))
    ):
        return False
    # Location conditions remain visible in the returned metadata. Search replay
    # does not silently reapply a current location decision to archived content.
    return True


def _safe_failure(attempt: ResolutionAttempt) -> Exception:
    category = attempt.error_category
    if category == PageFailureCategory.REMOVED:
        return PageRemovedError("archived failure: removed")
    if category == PageFailureCategory.BLOCKED:
        return PageHttpError("archived failure: blocked", status_code=attempt.http_status or 403)
    if category == PageFailureCategory.TRANSIENT:
        if attempt.http_status is not None and attempt.http_status >= 500:
            return PageHttpError("archived failure: transient", status_code=attempt.http_status)
        return PageTimeoutError("archived failure: transient")
    if category == PageFailureCategory.EMPTY_SHELL:
        return PageEmptyError("archived failure: empty shell")
    return ReplayedFailure(category or PageFailureCategory.UNKNOWN)
