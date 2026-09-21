from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

RunStatus = Literal["queued", "running", "succeeded", "partial", "failed"]

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    "queued": {"queued", "running", "failed"},
    "running": {"running", "succeeded", "partial", "failed"},
    "succeeded": {"succeeded"},
    "partial": {"partial"},
    "failed": {"failed"},
}


class RunStoreError(Exception):
    """Base error for persistent run metadata."""


class RunNotFoundError(RunStoreError, KeyError):
    """Raised when a run does not exist for the supplied owner."""


class InvalidRunTransition(RunStoreError, ValueError):
    """Raised when a run status would move backwards or leave a terminal state."""


class RunRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    owner_id: str
    status: RunStatus = "queued"
    data_mode: str
    request_summary: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    error_summary: str = ""
    created_at: datetime
    updated_at: datetime
    retention_deadline: datetime

    @field_validator("run_id", "owner_id", "data_mode")
    @classmethod
    def require_non_blank_identifier(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("created_at", "updated_at", "retention_deadline")
    @classmethod
    def require_timezone_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(UTC)


class RunStore(Protocol):
    def create_run(
        self,
        record: RunRecord,
        *,
        idempotency_key: str | None = None,
    ) -> RunRecord: ...

    def get_run(self, owner_id: str, run_id: str) -> RunRecord | None: ...

    def list_runs(self, owner_id: str, *, limit: int = 10) -> list[RunRecord]: ...

    def update_run(
        self,
        owner_id: str,
        run_id: str,
        *,
        status: RunStatus,
        metrics: Mapping[str, Any] | None = None,
        error_summary: str | None = None,
        updated_at: datetime | None = None,
        retention_deadline: datetime | None = None,
    ) -> RunRecord: ...

    def cleanup_expired(self, *, now: datetime | None = None) -> int: ...


def _safe_segment(value: str, field_name: str) -> str:
    value = value.strip()
    if not _SAFE_SEGMENT.fullmatch(value):
        raise ValueError(f"{field_name} contains unsafe path characters")
    return value


def _normalize_now(now: datetime | None) -> datetime:
    value = now or datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


class FileRunStore:
    """Filesystem adapter used by local development and restart simulations."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create_run(
        self,
        record: RunRecord,
        *,
        idempotency_key: str | None = None,
    ) -> RunRecord:
        owner_id = _safe_segment(record.owner_id, "owner_id")
        run_id = _safe_segment(record.run_id, "run_id")
        key = idempotency_key.strip() if idempotency_key else None
        if idempotency_key is not None and not key:
            raise ValueError("idempotency_key must not be blank")

        existing = self.get_run(owner_id, run_id)
        if existing is not None:
            existing_key = self._idempotency_keys(owner_id).get(key or "")
            if key and existing_key == run_id:
                return existing
            if key is None and existing == record:
                return existing
            raise ValueError("run_id already exists for this owner")

        if key:
            indexed = self._idempotency_keys(owner_id)
            indexed_run_id = indexed.get(key)
            if indexed_run_id:
                indexed_record = self.get_run(owner_id, indexed_run_id)
                if indexed_record is not None:
                    return indexed_record
                indexed.pop(key, None)

        _write_json(self._run_path(owner_id, run_id), record.model_dump(mode="json"))
        if key:
            indexed = self._idempotency_keys(owner_id)
            indexed[key] = run_id
            self._write_idempotency_keys(owner_id, indexed)
        return record

    def get_run(self, owner_id: str, run_id: str) -> RunRecord | None:
        owner_id = _safe_segment(owner_id, "owner_id")
        run_id = _safe_segment(run_id, "run_id")
        path = self._run_path(owner_id, run_id)
        if not path.is_file():
            return None
        record = RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
        if record.owner_id != owner_id or record.run_id != run_id:
            return None
        return record

    def list_runs(self, owner_id: str, *, limit: int = 10) -> list[RunRecord]:
        owner_id = _safe_segment(owner_id, "owner_id")
        if limit <= 0:
            return []
        owner_dir = self.root / owner_id
        if not owner_dir.is_dir():
            return []
        records: list[RunRecord] = []
        for path in owner_dir.glob("*.json"):
            if path.name == "_idempotency.json":
                continue
            try:
                record = RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if record.owner_id == owner_id:
                records.append(record)
        records.sort(key=lambda item: (item.created_at, item.run_id), reverse=True)
        return records[:limit]

    def update_run(
        self,
        owner_id: str,
        run_id: str,
        *,
        status: RunStatus,
        metrics: Mapping[str, Any] | None = None,
        error_summary: str | None = None,
        updated_at: datetime | None = None,
        retention_deadline: datetime | None = None,
    ) -> RunRecord:
        current = self.get_run(owner_id, run_id)
        if current is None:
            raise RunNotFoundError(f"run not found: {owner_id}/{run_id}")
        if status not in _ALLOWED_TRANSITIONS[current.status]:
            raise InvalidRunTransition(
                f"invalid run transition: {current.status} -> {status}"
            )
        timestamp = _normalize_now(updated_at)
        update: dict[str, Any] = {
            "status": status,
            "updated_at": timestamp,
        }
        if metrics is not None:
            update["metrics"] = dict(metrics)
        if error_summary is not None:
            update["error_summary"] = error_summary
        if retention_deadline is not None:
            update["retention_deadline"] = retention_deadline
        updated = current.model_copy(update=update)
        _write_json(
            self._run_path(owner_id, run_id),
            updated.model_dump(mode="json"),
        )
        return updated

    def cleanup_expired(self, *, now: datetime | None = None) -> int:
        current = _normalize_now(now)
        deleted = 0
        for owner_dir in self.root.iterdir():
            if not owner_dir.is_dir():
                continue
            indexed = self._idempotency_keys(owner_dir.name)
            index_changed = False
            for path in owner_dir.glob("*.json"):
                if path.name == "_idempotency.json":
                    continue
                try:
                    record = RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if record.retention_deadline > current:
                    continue
                path.unlink(missing_ok=True)
                deleted += 1
                for key, run_id in list(indexed.items()):
                    if run_id == record.run_id:
                        del indexed[key]
                        index_changed = True
            if index_changed:
                self._write_idempotency_keys(owner_dir.name, indexed)
        return deleted

    def _run_path(self, owner_id: str, run_id: str) -> Path:
        return self.root / owner_id / f"{run_id}.json"

    def _idempotency_path(self, owner_id: str) -> Path:
        return self.root / owner_id / "_idempotency.json"

    def _idempotency_keys(self, owner_id: str) -> dict[str, str]:
        path = self._idempotency_path(owner_id)
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): str(value) for key, value in payload.items()}

    def _write_idempotency_keys(self, owner_id: str, keys: Mapping[str, str]) -> None:
        _write_json(self._idempotency_path(owner_id), keys)


class PostgresRunStore:
    """PostgreSQL-compatible run metadata adapter with lazy psycopg imports."""

    def __init__(self, database_url: str, *, connect: Any | None = None):
        if not database_url.strip():
            raise ValueError("database_url must not be blank")
        self.database_url = database_url
        if connect is None:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:  # pragma: no cover - depends on optional extra
                raise RuntimeError(
                    "Postgres persistence requires the optional 'psycopg' dependency"
                ) from exc
            self._connect = lambda: psycopg.connect(
                self.database_url,
                row_factory=dict_row,
            )
        else:
            self._connect = connect

    def initialize(self) -> None:
        schema = (Path(__file__).with_name("persistence_schema.sql")).read_text(
            encoding="utf-8"
        )
        with self._connection() as conn:
            for statement in _sql_statements(schema):
                conn.execute(statement)

    def create_run(
        self,
        record: RunRecord,
        *,
        idempotency_key: str | None = None,
    ) -> RunRecord:
        key = idempotency_key.strip() if idempotency_key else None
        if idempotency_key is not None and not key:
            raise ValueError("idempotency_key must not be blank")
        with self._connection() as conn:
            if key:
                existing = conn.execute(
                    """
                    SELECT * FROM runs
                    WHERE owner_id = %s AND idempotency_key = %s
                    """,
                    (record.owner_id, key),
                ).fetchone()
                if existing is not None:
                    return _postgres_record(existing)
            try:
                row = conn.execute(
                    """
                    INSERT INTO runs (
                        run_id, owner_id, status, data_mode, request_summary,
                        metrics, error_summary, created_at, updated_at,
                        retention_deadline, idempotency_key
                    )
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        record.run_id,
                        record.owner_id,
                        record.status,
                        record.data_mode,
                        json.dumps(record.request_summary, ensure_ascii=False),
                        json.dumps(record.metrics, ensure_ascii=False),
                        record.error_summary,
                        record.created_at,
                        record.updated_at,
                        record.retention_deadline,
                        key,
                    ),
                ).fetchone()
            except Exception:
                if not key:
                    raise
                existing = conn.execute(
                    """
                    SELECT * FROM runs
                    WHERE owner_id = %s AND idempotency_key = %s
                    """,
                    (record.owner_id, key),
                ).fetchone()
                if existing is None:
                    raise
                return _postgres_record(existing)
            return _postgres_record(row)

    def get_run(self, owner_id: str, run_id: str) -> RunRecord | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE owner_id = %s AND run_id = %s",
                (owner_id, run_id),
            ).fetchone()
        return _postgres_record(row) if row is not None else None

    def list_runs(self, owner_id: str, *, limit: int = 10) -> list[RunRecord]:
        if limit <= 0:
            return []
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM runs
                WHERE owner_id = %s
                ORDER BY created_at DESC, run_id DESC
                LIMIT %s
                """,
                (owner_id, limit),
            ).fetchall()
        return [_postgres_record(row) for row in rows]

    def update_run(
        self,
        owner_id: str,
        run_id: str,
        *,
        status: RunStatus,
        metrics: Mapping[str, Any] | None = None,
        error_summary: str | None = None,
        updated_at: datetime | None = None,
        retention_deadline: datetime | None = None,
    ) -> RunRecord:
        current = self.get_run(owner_id, run_id)
        if current is None:
            raise RunNotFoundError(f"run not found: {owner_id}/{run_id}")
        if status not in _ALLOWED_TRANSITIONS[current.status]:
            raise InvalidRunTransition(
                f"invalid run transition: {current.status} -> {status}"
            )
        timestamp = _normalize_now(updated_at)
        with self._connection() as conn:
            row = conn.execute(
                """
                UPDATE runs
                SET status = %s,
                    metrics = COALESCE(%s::jsonb, metrics),
                    error_summary = COALESCE(%s, error_summary),
                    updated_at = %s,
                    retention_deadline = COALESCE(%s, retention_deadline)
                WHERE owner_id = %s AND run_id = %s
                RETURNING *
                """,
                (
                    status,
                    (
                        json.dumps(dict(metrics), ensure_ascii=False)
                        if metrics is not None
                        else None
                    ),
                    error_summary,
                    timestamp,
                    retention_deadline,
                    owner_id,
                    run_id,
                ),
            ).fetchone()
        if row is None:
            raise RunNotFoundError(f"run not found: {owner_id}/{run_id}")
        return _postgres_record(row)

    def cleanup_expired(self, *, now: datetime | None = None) -> int:
        current = _normalize_now(now)
        with self._connection() as conn:
            rows = conn.execute(
                """
                DELETE FROM runs
                WHERE retention_deadline <= %s
                RETURNING run_id
                """,
                (current,),
            ).fetchall()
        return len(rows)

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _postgres_record(row: Mapping[str, Any]) -> RunRecord:
    def json_field(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        return {}

    return RunRecord(
        run_id=str(row["run_id"]),
        owner_id=str(row["owner_id"]),
        status=row["status"],
        data_mode=str(row["data_mode"]),
        request_summary=json_field(row["request_summary"]),
        metrics=json_field(row["metrics"]),
        error_summary=str(row["error_summary"] or ""),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        retention_deadline=row["retention_deadline"],
    )


def _sql_statements(script: str) -> list[str]:
    return [statement.strip() for statement in script.split(";") if statement.strip()]
