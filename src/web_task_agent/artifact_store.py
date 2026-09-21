from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, field_validator

from web_task_agent.run_store import _SAFE_SEGMENT, _normalize_now


class ArtifactStoreError(Exception):
    """Base error for persisted run artifacts."""


class ArtifactNotFoundError(ArtifactStoreError, FileNotFoundError):
    """Raised when an artifact is absent or not owned by the supplied owner."""


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_key: str
    owner_id: str
    run_id: str
    object_key: str
    display_name: str
    mime: str
    size_bytes: int
    sha256: str
    created_at: datetime
    retention_deadline: datetime

    @field_validator("created_at", "retention_deadline")
    @classmethod
    def require_timezone_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(UTC)


class ArtifactStore(Protocol):
    def put_bytes(
        self,
        owner_id: str,
        run_id: str,
        artifact_key: str,
        content: bytes,
        *,
        display_name: str,
        mime: str,
        retention_deadline: datetime,
    ) -> ArtifactRecord: ...

    def get_artifact(
        self,
        owner_id: str,
        run_id: str,
        artifact_key: str,
    ) -> tuple[ArtifactRecord, bytes]: ...

    def list_artifacts(self, owner_id: str, run_id: str) -> list[ArtifactRecord]: ...

    def cleanup_expired(self, *, now: datetime | None = None) -> int: ...


def _safe_segment(value: str, field_name: str) -> str:
    value = value.strip()
    if not _SAFE_SEGMENT.fullmatch(value):
        raise ValueError(f"{field_name} contains unsafe path characters")
    return value


def _safe_display_name(value: str) -> str:
    value = value.strip()
    if not value or value in {".", ".."} or Path(value).name != value:
        raise ValueError("display_name must be a plain file name")
    return value


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    _write_bytes(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
    )


class FileArtifactStore:
    """Filesystem object-store adapter with deterministic private object keys."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(
        self,
        owner_id: str,
        run_id: str,
        artifact_key: str,
        content: bytes,
        *,
        display_name: str,
        mime: str,
        retention_deadline: datetime,
    ) -> ArtifactRecord:
        owner_id = _safe_segment(owner_id, "owner_id")
        run_id = _safe_segment(run_id, "run_id")
        artifact_key = _safe_segment(artifact_key, "artifact_key")
        display_name = _safe_display_name(display_name)
        mime = mime.strip()
        if not mime:
            raise ValueError("mime must not be blank")
        deadline = _normalize_now(retention_deadline)
        record = ArtifactRecord(
            artifact_key=artifact_key,
            owner_id=owner_id,
            run_id=run_id,
            object_key=f"owners/{owner_id}/runs/{run_id}/artifacts/{artifact_key}",
            display_name=display_name,
            mime=mime,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            created_at=datetime.now(UTC),
            retention_deadline=deadline,
        )
        directory = self._artifact_dir(owner_id, run_id, artifact_key)
        _write_bytes(directory / "content", content)
        _write_json(
            directory / "metadata.json",
            record.model_dump(mode="json"),
        )
        return record

    def get_artifact(
        self,
        owner_id: str,
        run_id: str,
        artifact_key: str,
    ) -> tuple[ArtifactRecord, bytes]:
        record = self._load_record(owner_id, run_id, artifact_key)
        content_path = self._artifact_dir(owner_id, run_id, artifact_key) / "content"
        resolved = content_path.resolve(strict=False)
        if not resolved.is_relative_to(self.root.resolve()):
            raise ArtifactNotFoundError("产物路径不可访问。")
        if not content_path.is_file():
            raise ArtifactNotFoundError("产物文件不存在。")
        return record, content_path.read_bytes()

    def list_artifacts(self, owner_id: str, run_id: str) -> list[ArtifactRecord]:
        owner_id = _safe_segment(owner_id, "owner_id")
        run_id = _safe_segment(run_id, "run_id")
        directory = self._run_artifacts_dir(owner_id, run_id)
        if not directory.is_dir():
            return []
        records: list[ArtifactRecord] = []
        for child in directory.iterdir():
            if not child.is_dir():
                continue
            try:
                records.append(self._load_record(owner_id, run_id, child.name))
            except (ArtifactNotFoundError, OSError, ValueError):
                continue
        records.sort(key=lambda item: item.artifact_key)
        return records

    def cleanup_expired(self, *, now: datetime | None = None) -> int:
        current = _normalize_now(now)
        deleted = 0
        if not (self.root / "owners").is_dir():
            return 0
        for metadata_path in self.root.glob("owners/*/runs/*/artifacts/*/metadata.json"):
            try:
                record = ArtifactRecord.model_validate_json(
                    metadata_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            if record.retention_deadline > current:
                continue
            shutil.rmtree(metadata_path.parent, ignore_errors=True)
            deleted += 1
        return deleted

    def _load_record(
        self,
        owner_id: str,
        run_id: str,
        artifact_key: str,
    ) -> ArtifactRecord:
        owner_id = _safe_segment(owner_id, "owner_id")
        run_id = _safe_segment(run_id, "run_id")
        artifact_key = _safe_segment(artifact_key, "artifact_key")
        metadata_path = self._artifact_dir(owner_id, run_id, artifact_key) / "metadata.json"
        if not metadata_path.is_file():
            raise ArtifactNotFoundError("产物不存在。")
        try:
            record = ArtifactRecord.model_validate_json(
                metadata_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise ArtifactNotFoundError("产物不存在。") from exc
        if (
            record.owner_id != owner_id
            or record.run_id != run_id
            or record.artifact_key != artifact_key
        ):
            raise ArtifactNotFoundError("产物不存在。")
        return record

    def _run_artifacts_dir(self, owner_id: str, run_id: str) -> Path:
        return self.root / "owners" / owner_id / "runs" / run_id / "artifacts"

    def _artifact_dir(self, owner_id: str, run_id: str, artifact_key: str) -> Path:
        return self._run_artifacts_dir(owner_id, run_id) / artifact_key
