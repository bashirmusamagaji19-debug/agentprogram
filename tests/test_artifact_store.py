from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime, timedelta

import pytest

from web_task_agent.artifact_store import (
    ArtifactNotFoundError,
    ArtifactRecord,
    FileArtifactStore,
    S3ArtifactStore,
)


def test_file_artifact_store_persists_content_and_metadata(tmp_path) -> None:
    store = FileArtifactStore(tmp_path)
    content = "报告内容".encode()
    retention = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)

    record = store.put_bytes(
        "owner-a",
        "run-1",
        "report",
        content,
        display_name="report.md",
        mime="text/markdown",
        retention_deadline=retention,
    )
    loaded_record, loaded_content = store.get_artifact("owner-a", "run-1", "report")

    assert loaded_record == record
    assert loaded_content == content
    assert record.sha256 == hashlib.sha256(content).hexdigest()
    assert record.size_bytes == len(content)
    assert record.object_key == "owners/owner-a/runs/run-1/artifacts/report"
    assert not record.object_key.startswith("/")


def test_file_artifact_store_isolates_artifacts_by_owner(tmp_path) -> None:
    store = FileArtifactStore(tmp_path)
    store.put_bytes(
        "owner-a",
        "run-1",
        "report",
        b"private",
        display_name="report.md",
        mime="text/markdown",
        retention_deadline=datetime(2026, 9, 28, tzinfo=UTC),
    )

    with pytest.raises(ArtifactNotFoundError):
        store.get_artifact("owner-b", "run-1", "report")
    assert store.list_artifacts("owner-b", "run-1") == []


def test_file_artifact_store_rejects_missing_content(tmp_path) -> None:
    store = FileArtifactStore(tmp_path)
    store.put_bytes(
        "owner-a",
        "run-1",
        "report",
        b"report",
        display_name="report.md",
        mime="text/markdown",
        retention_deadline=datetime(2026, 9, 28, tzinfo=UTC),
    )
    content_path = tmp_path / "owners" / "owner-a" / "runs" / "run-1" / "artifacts" / "report" / "content"
    content_path.unlink()

    with pytest.raises(ArtifactNotFoundError, match="不存在"):
        store.get_artifact("owner-a", "run-1", "report")


def test_file_artifact_store_rejects_unsafe_artifact_key(tmp_path) -> None:
    store = FileArtifactStore(tmp_path)

    with pytest.raises(ValueError, match="artifact_key"):
        store.put_bytes(
            "owner-a",
            "run-1",
            "../secret",
            b"do not expose",
            display_name="secret.txt",
            mime="text/plain",
            retention_deadline=datetime(2026, 9, 28, tzinfo=UTC),
        )


def test_file_artifact_store_cleans_expired_artifacts(tmp_path) -> None:
    store = FileArtifactStore(tmp_path)
    now = datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
    store.put_bytes(
        "owner-a",
        "run-expired",
        "report",
        b"expired",
        display_name="report.md",
        mime="text/markdown",
        retention_deadline=now - timedelta(seconds=1),
    )
    store.put_bytes(
        "owner-a",
        "run-live",
        "report",
        b"live",
        display_name="report.md",
        mime="text/markdown",
        retention_deadline=now + timedelta(days=1),
    )

    deleted = store.cleanup_expired(now=now)

    assert deleted == 1
    with pytest.raises(ArtifactNotFoundError):
        store.get_artifact("owner-a", "run-expired", "report")
    assert store.get_artifact("owner-a", "run-live", "report")[1] == b"live"


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **kwargs) -> None:  # noqa: N803
        self.objects[Key] = bytes(Body)

    def get_object(self, *, Bucket: str, Key: str):  # noqa: N803
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, *, Bucket: str, Prefix: str):  # noqa: N803
        return {
            "Contents": [
                {"Key": key}
                for key in sorted(self.objects)
                if key.startswith(Prefix)
            ],
            "IsTruncated": False,
        }

    def delete_object(self, *, Bucket: str, Key: str) -> None:  # noqa: N803
        self.objects.pop(Key, None)


def test_s3_artifact_store_round_trips_private_object_and_metadata() -> None:
    client = FakeS3Client()
    store = S3ArtifactStore("private-bucket", client=client)
    content = b"report"

    record = store.put_bytes(
        "owner-a",
        "run-1",
        "report",
        content,
        display_name="report.md",
        mime="text/markdown",
        retention_deadline=datetime(2026, 9, 28, tzinfo=UTC),
    )
    loaded_record, loaded_content = store.get_artifact("owner-a", "run-1", "report")

    assert loaded_record == record
    assert loaded_content == content
    assert record.object_key == "owners/owner-a/runs/run-1/artifacts/report"
    assert all(not key.startswith("/") for key in client.objects)
    assert all("owner-b" not in key for key in client.objects)


def test_s3_artifact_store_cleans_only_expired_metadata() -> None:
    client = FakeS3Client()
    store = S3ArtifactStore("private-bucket", client=client)
    now = datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
    store.put_bytes(
        "owner-a",
        "run-expired",
        "report",
        b"expired",
        display_name="report.md",
        mime="text/markdown",
        retention_deadline=now - timedelta(seconds=1),
    )
    store.put_bytes(
        "owner-a",
        "run-live",
        "report",
        b"live",
        display_name="report.md",
        mime="text/markdown",
        retention_deadline=now + timedelta(days=1),
    )

    deleted = store.cleanup_expired(now=now)

    assert deleted == 1
    with pytest.raises(ArtifactNotFoundError):
        store.get_artifact("owner-a", "run-expired", "report")
    assert store.get_artifact("owner-a", "run-live", "report")[1] == b"live"


def test_s3_artifact_store_rejects_metadata_owner_mismatch() -> None:
    client = FakeS3Client()
    store = S3ArtifactStore("private-bucket", client=client)
    record = store.put_bytes(
        "owner-a",
        "run-1",
        "report",
        b"report",
        display_name="report.md",
        mime="text/markdown",
        retention_deadline=datetime(2026, 9, 28, tzinfo=UTC),
    )
    tampered = record.model_copy(update={"owner_id": "owner-b"})
    client.objects[
        "owners/owner-a/runs/run-1/artifacts/report/metadata.json"
    ] = tampered.model_dump_json().encode()

    with pytest.raises(ArtifactNotFoundError):
        store.get_artifact("owner-a", "run-1", "report")


def test_artifact_record_requires_timezone_aware_retention() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ArtifactRecord(
            artifact_key="report",
            owner_id="owner-a",
            run_id="run-1",
            object_key="owners/owner-a/runs/run-1/artifacts/report",
            display_name="report.md",
            mime="text/markdown",
            size_bytes=6,
            sha256="a" * 64,
            created_at=datetime(2026, 9, 21, 8, 0),
            retention_deadline=datetime(2026, 9, 28, 8, 0),
        )
