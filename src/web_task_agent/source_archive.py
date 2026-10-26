"""Append-oriented, hash-verified storage for source provenance."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from web_task_agent.models import BrowserPage
from web_task_agent.page_reliability import (
    PageFailure,
    classify_page_exception,
)
from web_task_agent.source_provenance import (
    SOURCE_CONTEXT_KEYS,
    ArchiveManifest,
    CandidateEvidence,
    ManifestRevision,
    ResolutionAttempt,
    SnapshotEvidence,
    candidate_record_sha256,
    input_identity_hash,
    manifest_revision_sha256,
    normalize_source_context,
    sha256_text,
    snapshot_identity_hash,
    source_context_sha256,
    validate_safe_id,
    validate_safe_relative_path,
    validate_sha256,
)


class SourceArchiveError(Exception):
    """Base error for source archive persistence."""


class ArchiveNotFoundError(SourceArchiveError):
    """A referenced candidate, snapshot, body, or manifest is absent."""


class ArchiveCorruptionError(SourceArchiveError):
    """Stored bytes disagree with their immutable record or hash."""


class ArchiveSchemaError(SourceArchiveError):
    """The record uses an unsupported or malformed schema."""


class ArchiveConflictError(SourceArchiveError):
    """An immutable identity already exists with different content."""


_OBSERVATION_FIELDS = frozenset(
    {
        "captured_at",
        "observed_at",
        "content_origin",
        "canonical_url",
        "evidence_level",
        "availability",
        "cache_age_seconds",
    }
)
_PAGE_ORIGIN_ALIASES = {
    # Existing loaders expose these aliases to extractor url_verification.
    # Keep the alias in replay metadata, but compare its provenance canonically.
    "jd_text-fallback": "list",
    "detail-probed": "list",
}
_PUBLIC_PAGE_ORIGINS = frozenset({"official-api", "http", "list", "cache"})
_ATTEMPT_FIELDS = frozenset(
    {
        "candidate_id",
        "sequence",
        "strategy",
        "started_at",
        "elapsed_ms",
        "status",
        "error_category",
        "category",
        "http_status",
        "snapshot_id",
        "detail",
        "error_detail",
        "error_type",
        "schema_version",
        "operation_id",
        "operation_kind",
    }
)
_LOCKS_GUARD = threading.Lock()
_ARCHIVE_LOCKS: dict[Path, threading.RLock] = {}
_OS_LOCK_LOCAL = threading.local()


def _native_path(path: Path) -> str:
    """Return a Windows long-path-safe path without changing public Path values."""

    text = str(path)
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return text
    absolute = os.path.abspath(text)
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def _path_exists(path: Path) -> bool:
    return os.path.exists(_native_path(path))


def _path_is_file(path: Path) -> bool:
    return os.path.isfile(_native_path(path))


def _read_path_bytes(path: Path) -> bytes:
    with open(_native_path(path), "rb") as stream:
        return stream.read()


def _make_directory(path: Path) -> None:
    os.makedirs(_native_path(path), exist_ok=True)


def _shared_archive_lock(root: Path) -> threading.RLock:
    with _LOCKS_GUARD:
        return _ARCHIVE_LOCKS.setdefault(root, threading.RLock())


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _decode_json(raw: bytes, description: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveCorruptionError(f"invalid UTF-8 JSON in {description}") from exc


def _schema_version(payload: Any, description: str) -> None:
    if not isinstance(payload, dict):
        raise ArchiveSchemaError(f"{description} must be a JSON object")
    if payload.get("schema_version") != 1:
        raise ArchiveSchemaError(f"unsupported or missing schema_version in {description}")


_JSON_DATETIME_FIELDS: dict[type, tuple[str, ...]] = {
    CandidateEvidence: ("discovered_at",),
    SnapshotEvidence: ("captured_at", "observed_at"),
    ResolutionAttempt: ("started_at",),
    ArchiveManifest: ("created_at",),
    ManifestRevision: ("created_at",),
}


def _model_from_json(model: type, payload: dict[str, Any], description: str) -> Any:
    """Restore JSON timestamps without permitting coercion of other fields."""

    restored = dict(payload)
    for field_name in _JSON_DATETIME_FIELDS[model]:
        value = restored.get(field_name)
        if value is None or isinstance(value, datetime):
            continue
        if not isinstance(value, str):
            raise ArchiveSchemaError(f"{description} {field_name} must be an ISO timestamp")
        try:
            restored[field_name] = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ArchiveSchemaError(f"{description} has an invalid {field_name}") from exc
    try:
        return model.model_validate(restored)
    except ValueError as exc:
        raise ArchiveSchemaError(f"invalid schema in {description}") from exc


class SourceArchive:
    """A single, immutable run archive rooted at ``root``.

    Candidate history, attempt history and body snapshots are append-only.
    ``manifest.json`` is replaced atomically and may only add referenced IDs on
    a resume; its run identity and creation time cannot change.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        # Reading an archive must not create or modify it; writers create only
        # the directories needed for their own atomic/append operation.
        self._root_resolved = self.root.resolve(strict=False)
        self._lock = _shared_archive_lock(self._root_resolved)

    @contextlib.contextmanager
    def _locked(self):
        """Serialize a read-modify-publish transaction across processes."""

        self._lock.acquire()
        state = getattr(_OS_LOCK_LOCAL, "state", None)
        if state is None:
            state = {}
            _OS_LOCK_LOCAL.state = state
        key = str(self._root_resolved)
        stream = None
        try:
            existing = state.get(key)
            if existing is not None:
                existing[0] += 1
            else:
                _make_directory(self.root)
                lock_path = self.root / ".archive.lock"
                stream = lock_path.open("a+b")
                if stream.seek(0, os.SEEK_END) == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                state[key] = [1, stream]
            yield
        finally:
            try:
                current = state.get(key)
                if current is not None:
                    current[0] -= 1
                    if current[0] == 0:
                        held = current[1]
                        try:
                            if os.name == "nt":
                                import msvcrt

                                held.seek(0)
                                msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, 1)
                            else:
                                import fcntl

                                fcntl.flock(held.fileno(), fcntl.LOCK_UN)
                        finally:
                            held.close()
                            del state[key]
                elif stream is not None:
                    stream.close()
            finally:
                self._lock.release()

    @property
    def candidates_path(self) -> Path:
        return self.root / "candidates.jsonl"

    @property
    def attempts_path(self) -> Path:
        return self.root / "attempts.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def revisions_path(self) -> Path:
        return self.root / "manifest-revisions.jsonl"

    def write_candidate(self, evidence: CandidateEvidence | Mapping[str, Any]) -> str:
        record = CandidateEvidence.model_validate(
            evidence.model_dump() if isinstance(evidence, CandidateEvidence) else evidence
        )
        payload = _json_bytes(record.model_dump(mode="json"))
        with self._locked():
            previous = [item for item in self.read_candidates() if item.candidate_id == record.candidate_id]
            if any(item.identity_sha256 != record.identity_sha256 for item in previous):
                raise ArchiveConflictError("candidate native identity cannot change for an existing ID")
            self._append_bytes(self.candidates_path, payload)
        return record.candidate_id

    def read_candidates(self) -> list[CandidateEvidence]:
        with self._lock:
            return self._read_jsonl(self.candidates_path, CandidateEvidence, "candidate history")

    def write_page(
        self,
        candidate_id: str,
        page: BrowserPage,
        observation: Mapping[str, Any],
    ) -> str:
        if not isinstance(page, BrowserPage):
            raise TypeError("page must be a BrowserPage")
        if not isinstance(observation, Mapping):
            raise TypeError("observation must be a mapping")
        unknown = set(observation) - _OBSERVATION_FIELDS
        if unknown:
            raise ValueError("observation contains fields outside the public whitelist")
        if "content_origin" not in observation:
            raise ValueError("observation requires content_origin")
        if "evidence_level" not in observation:
            raise ValueError("observation requires evidence_level")

        candidate_id = validate_safe_id(candidate_id, "candidate_id")
        with self._locked():
            versions = self._candidate_versions(candidate_id)
            if not versions:
                raise ArchiveNotFoundError(f"candidate is not recorded: {candidate_id}")

            try:
                body = page.content
                if not isinstance(body, str):
                    raise TypeError("page content must be text")
                raw_body = body.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("page content is not valid UTF-8 text") from exc
            body_sha256 = sha256_text(body)
            captured_at = observation.get("captured_at", datetime.now(UTC))
            observed_at = observation.get("observed_at")
            content_origin = observation["content_origin"]
            evidence_level = observation["evidence_level"]
            availability = observation.get("availability", "unknown")
            canonical_url = observation.get("canonical_url")
            cache_age_seconds = observation.get("cache_age_seconds")
            if not isinstance(captured_at, datetime):
                raise ValueError("observation.captured_at must be a datetime")
            if observed_at is not None and not isinstance(observed_at, datetime):
                raise ValueError("observation.observed_at must be a datetime")
            if not isinstance(content_origin, str):
                raise ValueError("observation.content_origin must be a string")
            candidate, record_hash = self._select_candidate_version(
                versions,
                page=page,
                canonical_url=canonical_url,
            )
            self._validate_page_context(candidate, page=page, snapshot_origin=content_origin)
            source_context = self._source_context_for_page(
                candidate,
                page=page,
                canonical_url=canonical_url,
            )
            context_hash = source_context_sha256(source_context)

            input_sha256 = input_identity_hash(
                page.title,
                body,
                candidate_id=candidate_id,
                requested_url=page.url,
                canonical_url=canonical_url,
                content_origin=content_origin,
                evidence_level=evidence_level,
                availability=availability,
                captured_at=captured_at,
                observed_at=observed_at,
                cache_age_seconds=cache_age_seconds,
                source_context=source_context,
                source_context_sha256=context_hash,
                candidate_record_sha256=record_hash,
            )
            snapshot_id = snapshot_identity_hash(input_sha256)
            snapshot = SnapshotEvidence(
                snapshot_id=snapshot_id,
                candidate_id=candidate_id,
                captured_at=captured_at,
                content_origin=content_origin,
                requested_url=page.url,
                canonical_url=canonical_url,
                title=page.title,
                body_relative_path=f"bodies/{body_sha256}.txt",
                body_sha256=body_sha256,
                input_sha256=input_sha256,
                evidence_level=evidence_level,
                availability=availability,
                observed_at=observed_at,
                cache_age_seconds=cache_age_seconds,
                source_context=source_context,
                source_context_sha256=context_hash,
                candidate_record_sha256=record_hash,
            )

            body_path = self._resolve_relative(snapshot.body_relative_path, "body")
            self._write_immutable(body_path, raw_body, "body")
            snapshot_path = self._resolve_relative(f"snapshots/{snapshot.snapshot_id}.json", "snapshot")
            self._write_immutable(
                snapshot_path,
                _json_bytes(snapshot.model_dump(mode="json")),
                "snapshot",
            )
            return snapshot.snapshot_id

    def write_failure(self, candidate_id: str, failure: Any) -> str:
        """Append safe failure metadata, discarding messages and response text."""

        candidate_id = validate_safe_id(candidate_id, "candidate_id")
        with self._locked():
            if not self._candidate_exists(candidate_id):
                raise ArchiveNotFoundError(f"candidate is not recorded: {candidate_id}")
            attempt = self._failure_attempt(candidate_id, failure)
            self._append_attempt(attempt)
        return "attempts.jsonl"

    def write_attempt(self, attempt: ResolutionAttempt | Mapping[str, Any]) -> str:
        """Append an attempt; mappings without a sequence allocate one safely."""

        values = attempt.model_dump() if isinstance(attempt, ResolutionAttempt) else dict(attempt)
        values.setdefault("started_at", datetime.now(UTC))
        values.setdefault("elapsed_ms", 0)
        with self._locked():
            candidate_id = values.get("candidate_id")
            if not isinstance(candidate_id, str):
                raise ValueError("attempt requires candidate_id")
            if not self._candidate_exists(candidate_id):
                raise ArchiveNotFoundError(f"candidate is not recorded: {candidate_id}")
            if "sequence" not in values:
                values["sequence"] = self._next_sequence(candidate_id)
            else:
                self._assert_contiguous_sequence(candidate_id, int(values["sequence"]))
            record = ResolutionAttempt.model_validate(values)
            if record.snapshot_id is not None and not self._snapshot_exists(record.snapshot_id):
                raise ArchiveNotFoundError(f"snapshot is not recorded: {record.snapshot_id}")
            self._append_bytes(self.attempts_path, _json_bytes(record.model_dump(mode="json")))
        return "attempts.jsonl"

    def append_attempt(self, attempt: Mapping[str, Any]) -> str:
        """Atomically allocate and publish an attempt, defaulting to success."""

        if not isinstance(attempt, Mapping):
            raise TypeError("append_attempt requires a mapping")
        values = dict(attempt)
        if "sequence" in values:
            raise ValueError("append_attempt allocates the sequence inside the archive lock")
        values.setdefault("status", "success")
        return self.write_attempt(values)

    def read_attempts(self) -> list[ResolutionAttempt]:
        with self._lock:
            return self._read_jsonl(self.attempts_path, ResolutionAttempt, "attempt history")

    def read_page(self, snapshot_id: str) -> BrowserPage:
        snapshot_id = validate_safe_id(snapshot_id, "snapshot_id")
        with self._lock:
            snapshot_path = self._resolve_relative(f"snapshots/{snapshot_id}.json", "snapshot")
            if not _path_is_file(snapshot_path):
                raise ArchiveNotFoundError(f"snapshot is not recorded: {snapshot_id}")
            payload = _decode_json(_read_path_bytes(snapshot_path), f"snapshot {snapshot_id}")
            _schema_version(payload, f"snapshot {snapshot_id}")
            evidence = _model_from_json(SnapshotEvidence, payload, f"snapshot schema: {snapshot_id}")
            if evidence.snapshot_id != snapshot_id:
                raise ArchiveCorruptionError("snapshot filename and record ID disagree")
            if not self._candidate_exists(evidence.candidate_id):
                raise ArchiveCorruptionError("snapshot references an unrecorded candidate")
            if evidence.candidate_record_sha256 is None:
                raise ArchiveCorruptionError("snapshot is missing its candidate record binding")
            candidate_versions = self._candidate_versions(evidence.candidate_id)
            matching_candidates = [
                (candidate, record_hash)
                for candidate, record_hash in candidate_versions
                if record_hash == evidence.candidate_record_sha256
            ]
            if not matching_candidates:
                raise ArchiveCorruptionError("snapshot candidate record binding is missing")
            bound_candidate = matching_candidates[0][0]

            validate_safe_relative_path(evidence.body_relative_path)
            body_path = self._resolve_relative(evidence.body_relative_path, "body")
            if not _path_is_file(body_path):
                raise ArchiveNotFoundError(f"snapshot body is missing: {snapshot_id}")
            body_bytes = _read_path_bytes(body_path)
            if sha256_text_bytes(body_bytes) != evidence.body_sha256:
                raise ArchiveCorruptionError(f"body hash mismatch for snapshot {snapshot_id}")
            try:
                body = body_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ArchiveCorruptionError(f"body is not UTF-8 for snapshot {snapshot_id}") from exc

            expected_input = input_identity_hash(
                evidence.title,
                body,
                candidate_id=evidence.candidate_id,
                requested_url=evidence.requested_url,
                canonical_url=evidence.canonical_url,
                content_origin=evidence.content_origin,
                evidence_level=evidence.evidence_level,
                availability=evidence.availability,
                captured_at=evidence.captured_at,
                observed_at=evidence.observed_at,
                cache_age_seconds=evidence.cache_age_seconds,
                source_context=evidence.source_context,
                source_context_sha256=evidence.source_context_sha256,
                candidate_record_sha256=evidence.candidate_record_sha256,
            )
            if expected_input != evidence.input_sha256:
                raise ArchiveCorruptionError(f"snapshot input hash mismatch: {snapshot_id}")
            if snapshot_identity_hash(evidence.input_sha256) != evidence.snapshot_id:
                raise ArchiveCorruptionError(f"snapshot identity mismatch: {snapshot_id}")

            metadata: dict[str, Any] = {
                "snapshot_id": evidence.snapshot_id,
                "candidate_id": evidence.candidate_id,
                "requested_url": evidence.requested_url,
                "canonical_url": evidence.canonical_url,
                "captured_at": _iso(evidence.captured_at),
                "observed_at": _iso(evidence.observed_at) if evidence.observed_at else None,
                "evidence_level": evidence.evidence_level,
                "availability": evidence.availability,
                "body_relative_path": evidence.body_relative_path,
                "body_sha256": evidence.body_sha256,
                "input_sha256": evidence.input_sha256,
                "cache_age_seconds": evidence.cache_age_seconds,
                "source_context_sha256": evidence.source_context_sha256,
                "candidate_record_sha256": evidence.candidate_record_sha256,
                "snapshot_content_origin": evidence.content_origin,
            }
            metadata.update(evidence.source_context)
            metadata.setdefault("source_spec", bound_candidate.source_spec)
            if bound_candidate.source_job_id is not None:
                metadata.setdefault("source_job_id", bound_candidate.source_job_id)
            if bound_candidate.company:
                metadata.setdefault("discovered_company", bound_candidate.company)
            if bound_candidate.location_hint:
                metadata.setdefault("discovered_location", bound_candidate.location_hint)
            page_source = str(metadata.get("page_source") or evidence.content_origin)
            return BrowserPage(
                url=evidence.requested_url,
                title=evidence.title,
                content=body,
                source=page_source,
                metadata=metadata,
            )

    def write_manifest(self, manifest: ArchiveManifest | Mapping[str, Any]) -> str:
        record = ArchiveManifest.model_validate(
            manifest.model_dump() if isinstance(manifest, ArchiveManifest) else manifest
        )
        if record.revision_sequence is not None or record.revision_sha256 is not None:
            raise ValueError("manifest revision fields are allocated by SourceArchive")
        if record.attempts_path != "attempts.jsonl":
            raise ValueError("manifest attempts_path must be attempts.jsonl")
        with self._locked():
            candidates = {item.candidate_id for item in self.read_candidates()}
            missing_candidates = sorted(set(record.candidate_ids) - candidates)
            if missing_candidates:
                raise ArchiveNotFoundError(
                    "manifest references unrecorded candidate(s): " + ", ".join(missing_candidates)
                )
            for snapshot_id in record.snapshot_ids:
                if not self._snapshot_exists(snapshot_id):
                    raise ArchiveNotFoundError(f"manifest references unrecorded snapshot: {snapshot_id}")
                page = self.read_page(snapshot_id)
                if page.metadata.get("candidate_id") not in record.candidate_ids:
                    raise ArchiveCorruptionError("manifest snapshot references a candidate outside its candidate_ids")
            actual_candidates = set(candidates)
            actual_snapshots = self._snapshot_ids_on_disk()
            if actual_candidates != set(record.candidate_ids):
                raise ArchiveConflictError("manifest candidate inventory does not match disk records")
            if actual_snapshots != set(record.snapshot_ids):
                raise ArchiveConflictError("manifest snapshot inventory does not match disk records")
            candidates_count, candidates_bytes, candidates_sha256 = self._candidate_inventory()
            if record.candidates_count is not None and record.candidates_count != candidates_count:
                raise ArchiveConflictError("manifest candidate inventory count does not match the ledger")
            if record.candidates_sha256 is not None and record.candidates_sha256 != candidates_sha256:
                raise ArchiveConflictError("manifest candidate inventory hash does not match the ledger")
            attempts_count, attempts_bytes, attempts_sha256 = self._attempt_inventory()
            if record.attempts_count is not None and record.attempts_count != attempts_count:
                raise ArchiveConflictError("manifest attempt inventory count does not match the ledger")
            if record.attempts_sha256 is not None and record.attempts_sha256 != attempts_sha256:
                raise ArchiveConflictError("manifest attempt inventory hash does not match the ledger")
            revisions = self._read_manifest_revisions()
            if _path_is_file(self.manifest_path) or revisions:
                previous = self.read_manifest()
                if (
                    previous.run_id != record.run_id
                    or previous.created_at != record.created_at
                    or previous.conditions != record.conditions
                    or previous.code_revision != record.code_revision
                    or previous.dirty_manifest_sha256 != record.dirty_manifest_sha256
                    or not set(previous.candidate_ids).issubset(record.candidate_ids)
                    or not set(previous.snapshot_ids).issubset(record.snapshot_ids)
                ):
                    raise ArchiveConflictError("manifest revision cannot replace prior run history")
                if previous.revision_sequence is None:
                    if revisions:
                        raise ArchiveCorruptionError("legacy manifest conflicts with revision ledger")
                    revisions = [self._bootstrap_manifest_revision(previous)]
                elif (
                    not revisions
                    or previous.revision_sequence != len(revisions)
                    or previous.revision_sha256 != manifest_revision_sha256(revisions[-1])
                ):
                    raise ArchiveCorruptionError("manifest revision anchor is inconsistent")
                self._assert_revision_contract(
                    revisions,
                    record,
                    candidates_count=candidates_count,
                    attempts_count=attempts_count,
                )
                if previous.revision_sequence is None:
                    self._append_manifest_revision(revisions[0])
                else:
                    prepared = _json_bytes(previous.model_dump(mode="json"))
                    if not _path_is_file(self.manifest_path) or _read_path_bytes(self.manifest_path) != prepared:
                        # Commit the validated tail before another revision can
                        # be prepared; repeated crashes leave at most one tail.
                        self._atomic_replace(self.manifest_path, prepared)

            revision = ManifestRevision(
                sequence=len(revisions) + 1,
                previous_revision_sha256=(
                    manifest_revision_sha256(revisions[-1]) if revisions else None
                ),
                run_id=record.run_id,
                conditions=record.conditions,
                candidate_ids=record.candidate_ids,
                snapshot_ids=record.snapshot_ids,
                attempts_path=record.attempts_path,
                created_at=record.created_at,
                code_revision=record.code_revision,
                dirty_manifest_sha256=record.dirty_manifest_sha256,
                candidates_count=candidates_count,
                candidates_bytes=candidates_bytes,
                candidates_sha256=candidates_sha256,
                attempts_count=attempts_count,
                attempts_bytes=attempts_bytes,
                attempts_sha256=attempts_sha256,
            )
            revision_sha256 = manifest_revision_sha256(revision)
            if revisions and revision.model_copy(update={
                "sequence": revisions[-1].sequence,
                "previous_revision_sha256": revisions[-1].previous_revision_sha256,
            }) == revisions[-1]:
                # Retrying publication (or an unchanged finalize) must not add
                # another history row. The validated unpublished row is reused.
                revision = revisions[-1]
                revision_sha256 = manifest_revision_sha256(revision)
            else:
                self._append_manifest_revision(revision)
            frozen = record.model_copy(
                update={
                    "candidates_count": candidates_count,
                    "candidates_sha256": candidates_sha256,
                    "attempts_count": attempts_count,
                    "attempts_sha256": attempts_sha256,
                    "revision_sequence": revision.sequence,
                    "revision_sha256": revision_sha256,
                }
            )
            self._atomic_replace(self.manifest_path, _json_bytes(frozen.model_dump(mode="json")))
        return "manifest.json"

    def read_manifest(self) -> ArchiveManifest:
        """Read the effective manifest, including one validated unpublished tail.

        A revision is durable before manifest.json is atomically replaced. A
        crash in that window leaves a complete prepared revision; reads validate
        and use it without modifying files, and the next write publishes it.
        """
        with self._lock:
            revisions = self._read_manifest_revisions()
            if _path_is_file(self.manifest_path):
                payload = _decode_json(_read_path_bytes(self.manifest_path), "manifest")
                _schema_version(payload, "manifest")
                record = _model_from_json(ArchiveManifest, payload, "manifest schema")
                if record.revision_sequence is not None:
                    anchor = record.revision_sequence
                    if (
                        anchor > len(revisions)
                        or len(revisions) - anchor not in {0, 1}
                        or record != self._manifest_for_revision(revisions[anchor - 1])
                    ):
                        raise ArchiveCorruptionError("manifest revision anchor is inconsistent")
                    if len(revisions) > anchor:
                        record = self._manifest_for_revision(revisions[-1])
                elif revisions:
                    raise ArchiveCorruptionError("legacy manifest conflicts with revision ledger")
            elif len(revisions) == 1:
                record = self._manifest_for_revision(revisions[0])
            elif revisions:
                raise ArchiveCorruptionError("manifest is missing with multiple unpublished revisions")
            else:
                raise ArchiveNotFoundError("manifest is not recorded")
            if revisions:
                self._assert_revision_contract(
                    revisions, record,
                    candidates_count=len(self.read_candidates()),
                    attempts_count=len(self.read_attempts()),
                )
            candidates = {item.candidate_id for item in self.read_candidates()}
            if not set(record.candidate_ids).issubset(candidates):
                raise ArchiveCorruptionError("manifest references an unrecorded candidate")
            if record.attempts_path != "attempts.jsonl":
                raise ArchiveCorruptionError("manifest attempts_path is not the archive ledger")
            listed_candidates = set(record.candidate_ids)
            for snapshot_id in record.snapshot_ids:
                page = self.read_page(snapshot_id)
                if page.metadata.get("candidate_id") not in listed_candidates:
                    raise ArchiveCorruptionError("manifest snapshot references an unlisted candidate")
            return record

    @staticmethod
    def _manifest_for_revision(revision: ManifestRevision) -> ArchiveManifest:
        fields = revision.model_dump(exclude={
            "sequence", "previous_revision_sha256", "candidates_bytes", "attempts_bytes",
        })
        return ArchiveManifest(**fields, revision_sequence=revision.sequence,
                               revision_sha256=manifest_revision_sha256(revision))

    def verify(self) -> dict[str, int]:
        """Verify record hashes and bidirectional inventory completeness."""

        with self._lock:
            candidates = self.read_candidates()
            attempts = self.read_attempts()
            manifest = self.read_manifest()
            known_candidates = {item.candidate_id for item in candidates}
            candidate_identities: dict[str, str] = {}
            for candidate in candidates:
                previous_identity = candidate_identities.setdefault(
                    candidate.candidate_id, candidate.identity_sha256 or ""
                )
                if previous_identity != (candidate.identity_sha256 or ""):
                    raise ArchiveCorruptionError("candidate native identity changed in the ledger")

            if set(manifest.candidate_ids) != known_candidates:
                raise ArchiveConflictError("manifest candidate inventory does not match disk records")
            actual_snapshots = self._snapshot_ids_on_disk()
            if set(manifest.snapshot_ids) != actual_snapshots:
                raise ArchiveConflictError("manifest snapshot inventory does not match disk records")
            candidates_count, _candidates_bytes, candidates_sha256 = self._candidate_inventory()
            if manifest.candidates_count != candidates_count or manifest.candidates_sha256 != candidates_sha256:
                raise ArchiveCorruptionError("candidate ledger count or hash does not match manifest")
            attempts_count, _attempts_bytes, attempts_sha256 = self._attempt_inventory()
            if manifest.attempts_count != attempts_count or manifest.attempts_sha256 != attempts_sha256:
                raise ArchiveCorruptionError("attempt ledger count or hash does not match manifest")

            revisions = self._read_manifest_revisions()
            if manifest.revision_sequence is None:
                if revisions:
                    raise ArchiveCorruptionError("legacy manifest conflicts with revision ledger")
                revisions = [self._bootstrap_manifest_revision(manifest)]
            elif (
                not revisions
                or manifest.revision_sequence != len(revisions)
                or manifest.revision_sha256 != manifest_revision_sha256(revisions[-1])
            ):
                raise ArchiveCorruptionError("manifest revision anchor is inconsistent")
            self._assert_revision_contract(
                revisions,
                manifest,
                candidates_count=candidates_count,
                attempts_count=attempts_count,
            )

            next_sequences: dict[str, int] = {}
            for attempt in attempts:
                if attempt.candidate_id not in known_candidates:
                    raise ArchiveCorruptionError("attempt references an unrecorded candidate")
                expected_sequence = next_sequences.get(attempt.candidate_id, 0) + 1
                if attempt.sequence != expected_sequence:
                    raise ArchiveCorruptionError("attempt sequence is not contiguous and chronological")
                next_sequences[attempt.candidate_id] = attempt.sequence
                if attempt.snapshot_id is not None:
                    page = self.read_page(attempt.snapshot_id)
                    if page.metadata.get("candidate_id") != attempt.candidate_id:
                        raise ArchiveCorruptionError("attempt snapshot belongs to another candidate")
            return {
                "candidates": len(candidates),
                "snapshots": len(manifest.snapshot_ids),
                "attempts": len(attempts),
            }

    def _failure_attempt(self, candidate_id: str, failure: Any) -> ResolutionAttempt:
        values: dict[str, Any]
        if isinstance(failure, ResolutionAttempt):
            if failure.candidate_id != candidate_id:
                raise ValueError("failure candidate_id disagrees with method argument")
            if failure.status.casefold() not in {"failure", "failed", "error"}:
                raise ValueError("write_failure requires a failed attempt")
            with self._locked():
                self._assert_contiguous_sequence(candidate_id, failure.sequence)
                if failure.snapshot_id is not None and not self._snapshot_exists(failure.snapshot_id):
                    raise ArchiveNotFoundError(f"snapshot is not recorded: {failure.snapshot_id}")
            return failure
        if isinstance(failure, Mapping):
            unknown = set(failure) - _ATTEMPT_FIELDS
            if unknown:
                raise ValueError("failure contains fields outside the safe attempt whitelist")
            values = {key: value for key, value in failure.items() if key not in {"detail", "error_detail"}}
        elif isinstance(failure, PageFailure):
            values = {
                "error_category": failure.category,
                "strategy": "page-failure",
            }
        elif isinstance(failure, Exception):
            values = {
                "error_category": classify_page_exception(failure).category,
                "strategy": type(failure).__name__,
                "http_status": getattr(failure, "status_code", None),
            }
        else:
            raise TypeError("failure must be an attempt, mapping, PageFailure, or Exception")

        if "category" in values:
            if "error_category" in values and values["error_category"] != values["category"]:
                raise ValueError("failure contains conflicting error categories")
            values["error_category"] = values.pop("category")
        if "error_type" in values:
            error_type = values.pop("error_type")
            values.setdefault("strategy", error_type)
        values["candidate_id"] = candidate_id
        values.setdefault("status", "failure")
        if not isinstance(values["status"], str) or values["status"].casefold() not in {
            "failure",
            "failed",
            "error",
        }:
            raise ValueError("write_failure requires failure status")
        values.setdefault("strategy", "unknown")
        values.setdefault("started_at", datetime.now(UTC))
        values.setdefault("elapsed_ms", 0)
        with self._locked():
            if "sequence" not in values:
                values["sequence"] = self._next_sequence(candidate_id)
            else:
                self._assert_contiguous_sequence(candidate_id, values["sequence"])
            if values.get("snapshot_id") is not None and not self._snapshot_exists(values["snapshot_id"]):
                raise ArchiveNotFoundError(f"snapshot is not recorded: {values['snapshot_id']}")
            return ResolutionAttempt.model_validate(values)

    def _append_attempt(self, attempt: ResolutionAttempt) -> None:
        with self._locked():
            self._append_bytes(self.attempts_path, _json_bytes(attempt.model_dump(mode="json")))

    def _snapshot_ids_on_disk(self) -> set[str]:
        directory = self.root / "snapshots"
        if not directory.is_dir():
            return set()
        snapshot_ids: set[str] = set()
        for path in directory.iterdir():
            if not _path_is_file(path) or path.suffix != ".json":
                continue
            snapshot_ids.add(validate_safe_id(path.stem, "snapshot_id"))
        return snapshot_ids

    def _attempt_inventory(self) -> tuple[int, int, str]:
        raw = _read_path_bytes(self.attempts_path) if _path_is_file(self.attempts_path) else b""
        rows = self.read_attempts()
        return len(rows), len(raw), hashlib.sha256(raw).hexdigest()

    def _candidate_inventory(self) -> tuple[int, int, str]:
        raw = _read_path_bytes(self.candidates_path) if _path_is_file(self.candidates_path) else b""
        rows = self.read_candidates()
        return len(rows), len(raw), hashlib.sha256(raw).hexdigest()

    def _read_manifest_revisions(self) -> list[ManifestRevision]:
        revisions = self._read_jsonl(
            self.revisions_path,
            ManifestRevision,
            "manifest revision history",
        )
        previous_sha256: str | None = None
        for index, revision in enumerate(revisions, 1):
            if revision.sequence != index:
                raise ArchiveCorruptionError("manifest revision sequence is not contiguous")
            if revision.previous_revision_sha256 != previous_sha256:
                raise ArchiveCorruptionError("manifest revision hash chain is broken")
            previous_sha256 = manifest_revision_sha256(revision)
        return revisions

    def _append_manifest_revision(self, revision: ManifestRevision) -> None:
        self._append_bytes(
            self.revisions_path,
            _json_bytes(revision.model_dump(mode="json")),
        )

    def _bootstrap_manifest_revision(self, previous: ArchiveManifest) -> ManifestRevision:
        if (
            previous.candidates_count is None
            or previous.candidates_sha256 is None
            or previous.attempts_count is None
            or previous.attempts_sha256 is None
        ):
            raise ArchiveCorruptionError("legacy manifest cannot safely anchor ledger history")
        candidates_bytes, candidates_sha256 = self._ledger_prefix_inventory(
            self.candidates_path,
            previous.candidates_count,
            "candidate history",
        )
        if candidates_sha256 != previous.candidates_sha256:
            raise ArchiveCorruptionError("candidate ledger prefix conflicts with legacy manifest")
        attempts_bytes, attempts_sha256 = self._ledger_prefix_inventory(
            self.attempts_path,
            previous.attempts_count,
            "attempt history",
        )
        if attempts_sha256 != previous.attempts_sha256:
            raise ArchiveCorruptionError("attempt ledger prefix conflicts with legacy manifest")
        return ManifestRevision(
            sequence=1,
            previous_revision_sha256=None,
            run_id=previous.run_id,
            conditions=previous.conditions,
            candidate_ids=previous.candidate_ids,
            snapshot_ids=previous.snapshot_ids,
            attempts_path=previous.attempts_path,
            created_at=previous.created_at,
            code_revision=previous.code_revision,
            dirty_manifest_sha256=previous.dirty_manifest_sha256,
            candidates_count=previous.candidates_count,
            candidates_bytes=candidates_bytes,
            candidates_sha256=previous.candidates_sha256,
            attempts_count=previous.attempts_count,
            attempts_bytes=attempts_bytes,
            attempts_sha256=previous.attempts_sha256,
        )

    def _assert_revision_contract(
        self,
        revisions: list[ManifestRevision],
        manifest: ArchiveManifest,
        *,
        candidates_count: int,
        attempts_count: int,
    ) -> None:
        if not revisions:
            return

        immutable = (
            manifest.run_id,
            manifest.created_at,
            manifest.conditions,
            manifest.code_revision,
            manifest.dirty_manifest_sha256,
            manifest.attempts_path,
        )
        for revision in revisions:
            if revision.attempts_path != manifest.attempts_path:
                raise ArchiveConflictError("manifest revision changed the attempt ledger path")
            if (
                revision.run_id,
                revision.created_at,
                revision.conditions,
                revision.code_revision,
                revision.dirty_manifest_sha256,
                revision.attempts_path,
            ) != immutable:
                raise ArchiveConflictError("manifest revision changed immutable run metadata")
            if revision.candidates_count > candidates_count:
                raise ArchiveCorruptionError("candidate ledger is shorter than its manifest revision")
            if revision.attempts_count > attempts_count:
                raise ArchiveCorruptionError("attempt ledger is shorter than its manifest revision")
            self._assert_ledger_prefix(
                self.candidates_path,
                revision.candidates_count,
                revision.candidates_bytes,
                revision.candidates_sha256,
                "candidate history",
            )
            self._assert_ledger_prefix(
                self.attempts_path,
                revision.attempts_count,
                revision.attempts_bytes,
                revision.attempts_sha256,
                "attempt history",
            )

        for previous, current in zip(revisions, revisions[1:], strict=False):
            if current.candidate_ids[: len(previous.candidate_ids)] != previous.candidate_ids:
                raise ArchiveConflictError("manifest revision candidate inventory is not append-only")
            if current.snapshot_ids[: len(previous.snapshot_ids)] != previous.snapshot_ids:
                raise ArchiveConflictError("manifest revision snapshot inventory is not append-only")
            if (
                current.candidates_count < previous.candidates_count
                or current.candidates_bytes < previous.candidates_bytes
                or current.attempts_count < previous.attempts_count
                or current.attempts_bytes < previous.attempts_bytes
            ):
                raise ArchiveConflictError("manifest revision ledger prefix moved backwards")

        latest = revisions[-1]
        if manifest.candidate_ids[: len(latest.candidate_ids)] != latest.candidate_ids:
            raise ArchiveConflictError("manifest candidate inventory is not append-only")
        if manifest.snapshot_ids[: len(latest.snapshot_ids)] != latest.snapshot_ids:
            raise ArchiveConflictError("manifest snapshot inventory is not append-only")

    def _ledger_prefix_inventory(
        self,
        path: Path,
        row_count: int,
        description: str,
    ) -> tuple[int, str]:
        raw = _read_path_bytes(path) if _path_is_file(path) else b""
        lines = raw.splitlines(keepends=True)
        if len(lines) < row_count:
            raise ArchiveCorruptionError(f"{description} is shorter than its manifest revision")
        prefix = b"".join(lines[:row_count])
        if prefix and not prefix.endswith(b"\n"):
            raise ArchiveCorruptionError(f"{description} revision ends with a partial record")
        return len(prefix), hashlib.sha256(prefix).hexdigest()

    def _assert_ledger_prefix(
        self,
        path: Path,
        row_count: int,
        byte_count: int,
        expected_sha256: str,
        description: str,
    ) -> None:
        raw = _read_path_bytes(path) if _path_is_file(path) else b""
        if byte_count > len(raw):
            raise ArchiveCorruptionError(f"{description} is shorter than its manifest revision")
        prefix = raw[:byte_count]
        if prefix and not prefix.endswith(b"\n"):
            raise ArchiveCorruptionError(f"{description} revision ends with a partial record")
        if len(prefix.splitlines()) != row_count:
            raise ArchiveCorruptionError(f"{description} revision row count is inconsistent")
        if hashlib.sha256(prefix).hexdigest() != expected_sha256:
            raise ArchiveCorruptionError(f"{description} prefix does not match its manifest revision")

    def _candidate_exists(self, candidate_id: str) -> bool:
        return any(item.candidate_id == candidate_id for item in self.read_candidates())

    def _candidate_versions(self, candidate_id: str) -> list[tuple[CandidateEvidence, str]]:
        return [
            (candidate, candidate_record_sha256(candidate))
            for candidate in self.read_candidates()
            if candidate.candidate_id == candidate_id
        ]

    def _source_context_for_page(
        self,
        candidate: CandidateEvidence,
        *,
        page: BrowserPage,
        canonical_url: str | None,
    ) -> dict[str, str | int]:
        context: dict[str, str | int] = {
            "source_spec": candidate.source_spec,
        }
        if candidate.source_job_id is not None:
            context["source_job_id"] = candidate.source_job_id
        if candidate.company:
            context["discovered_company"] = candidate.company
        if candidate.location_hint:
            context["discovered_location"] = candidate.location_hint
        canonical = page.metadata.get("canonical_url") or canonical_url or candidate.canonical_url
        if canonical:
            context["canonical_url"] = canonical
        if page.source:
            context["page_source"] = page.source
        for key in SOURCE_CONTEXT_KEYS:
            if key in page.metadata and page.metadata[key] is not None:
                context[key] = page.metadata[key]
        return normalize_source_context(context)

    @staticmethod
    def _validate_page_context(
        candidate: CandidateEvidence,
        *,
        page: BrowserPage,
        snapshot_origin: str,
    ) -> None:
        """Reject page metadata that contradicts the selected candidate.

        ``snapshot_origin`` describes how this archive obtained the page. Page
        metadata may preserve the underlying content origin when the snapshot
        itself came from cache, so only non-cache acquisitions require exact
        origin equality.
        """

        for key, expected in (
            ("source_spec", candidate.source_spec),
            ("source_job_id", candidate.source_job_id),
        ):
            if key not in page.metadata:
                continue
            actual = page.metadata[key]
            if actual != expected:
                raise ArchiveConflictError(
                    f"page metadata {key} conflicts with the selected candidate"
                )

        underlying_origin = page.metadata.get("content_origin")
        if underlying_origin is None:
            return
        if not isinstance(underlying_origin, str):
            raise ArchiveConflictError("page metadata content_origin is not a public origin")
        canonical_origin = _PAGE_ORIGIN_ALIASES.get(underlying_origin, underlying_origin)
        if canonical_origin not in _PUBLIC_PAGE_ORIGINS:
            raise ArchiveConflictError("page metadata content_origin is not a public origin")
        if snapshot_origin != "cache" and canonical_origin != snapshot_origin:
            raise ArchiveConflictError(
                "page metadata content_origin conflicts with the snapshot acquisition origin"
            )

    def _select_candidate_version(
        self,
        versions: list[tuple[CandidateEvidence, str]],
        *,
        page: BrowserPage,
        canonical_url: str | None,
    ) -> tuple[CandidateEvidence, str]:
        explicit_hash = page.metadata.get("candidate_record_sha256")
        if explicit_hash is not None:
            explicit_hash = validate_sha256(str(explicit_hash), "candidate_record_sha256")
            for candidate, record_hash in versions:
                if record_hash == explicit_hash:
                    return candidate, record_hash
            raise ArchiveConflictError("candidate_record_sha256 does not match this candidate")

        contexts = [
            (
                candidate,
                record_hash,
                source_context_sha256(
                    self._source_context_for_page(
                        candidate,
                        page=page,
                        canonical_url=canonical_url,
                    )
                ),
            )
            for candidate, record_hash in versions
        ]
        if len(versions) == 1:
            return versions[0]
        if len({context_hash for _, _, context_hash in contexts}) == 1:
            # City/query versions with identical extraction context are
            # equivalent for replay; bind the newest exact row.
            return versions[-1]
        raise ArchiveConflictError("ambiguous candidate version; set page.metadata['candidate_record_sha256']")

    def _snapshot_exists(self, snapshot_id: str) -> bool:
        snapshot_id = validate_safe_id(snapshot_id, "snapshot_id")
        return _path_is_file(self._resolve_relative(f"snapshots/{snapshot_id}.json", "snapshot"))

    def _assert_contiguous_sequence(self, candidate_id: str, sequence: int) -> None:
        expected = self._next_sequence(candidate_id)
        if sequence != expected:
            raise ArchiveConflictError(f"attempt sequence must be contiguous: expected {expected}, got {sequence}")

    def _next_sequence(self, candidate_id: str) -> int:
        rows = self.read_attempts()
        return (
            max(
                (row.sequence for row in rows if row.candidate_id == candidate_id),
                default=0,
            )
            + 1
        )

    def _read_jsonl(self, path: Path, model: type, description: str) -> list[Any]:
        if not _path_is_file(path):
            return []
        raw = _read_path_bytes(path)
        if not raw:
            return []
        if not raw.endswith(b"\n"):
            raise ArchiveCorruptionError(f"incomplete final record in {description}")
        records: list[Any] = []
        for line_number, line in enumerate(raw.splitlines(), 1):
            if not line:
                raise ArchiveCorruptionError(f"blank record in {description} at line {line_number}")
            payload = _decode_json(line, f"{description} line {line_number}")
            _schema_version(payload, f"{description} line {line_number}")
            records.append(_model_from_json(model, payload, f"{description} line {line_number}"))
        return records

    def _resolve_relative(self, relative: str, description: str) -> Path:
        relative = validate_safe_relative_path(relative)
        target = (self.root / Path(*relative.split("/"))).resolve(strict=False)
        if not target.is_relative_to(self._root_resolved):
            raise ArchiveCorruptionError(f"{description} path escapes the archive root")
        return target

    def _append_bytes(self, path: Path, content: bytes) -> None:
        _make_directory(path.parent)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        descriptor = os.open(_native_path(path), flags, 0o600)
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("archive append wrote no data")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write_immutable(self, path: Path, content: bytes, description: str) -> None:
        if _path_exists(path):
            if _read_path_bytes(path) != content:
                raise ArchiveConflictError(f"immutable {description} conflicts with existing bytes")
            return
        self._atomic_replace(path, content)

    def _atomic_replace(self, path: Path, content: bytes) -> None:
        _make_directory(path.parent)
        # Keep the scratch name short so immutable content paths near the
        # Windows MAX_PATH boundary do not fail only during the atomic write.
        descriptor, temporary_name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(_native_path(temporary), _native_path(path))
        finally:
            temporary.unlink(missing_ok=True)


def sha256_text_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _iso(value: datetime) -> str:
    rendered = value.astimezone(UTC).isoformat(timespec="microseconds")
    return rendered.replace(".000000+00:00", "Z").replace("+00:00", "Z")
