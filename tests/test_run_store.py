from __future__ import annotations

import importlib.util
import os
from datetime import UTC, datetime, timedelta

import pytest

from web_task_agent.run_store import (
    FileRunStore,
    InvalidRunTransition,
    RunNotFoundError,
    RunRecord,
)


def make_run(
    run_id: str = "run-1",
    owner_id: str = "owner-a",
    *,
    created_at: datetime | None = None,
    retention_deadline: datetime | None = None,
) -> RunRecord:
    created = created_at or datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
    return RunRecord(
        run_id=run_id,
        owner_id=owner_id,
        status="queued",
        data_mode="demo",
        request_summary={"keyword": "AI Agent", "target_count": 10},
        created_at=created,
        updated_at=created,
        retention_deadline=retention_deadline or created + timedelta(days=7),
    )


def test_file_run_store_round_trips_run_record(tmp_path) -> None:
    store = FileRunStore(tmp_path)
    record = make_run()

    created = store.create_run(record)

    assert created == record
    assert store.get_run("owner-a", "run-1") == record


def test_file_run_store_isolates_runs_by_owner(tmp_path) -> None:
    store = FileRunStore(tmp_path)
    store.create_run(make_run())

    assert store.get_run("owner-b", "run-1") is None
    assert store.list_runs("owner-b") == []


def test_file_run_store_create_is_idempotent_by_key(tmp_path) -> None:
    store = FileRunStore(tmp_path)
    first = make_run("run-1")
    second = make_run("run-2")

    created = store.create_run(first, idempotency_key="request-123")
    replayed = store.create_run(second, idempotency_key="request-123")

    assert replayed == created
    assert store.get_run("owner-a", "run-2") is None
    assert len(store.list_runs("owner-a")) == 1


def test_file_run_store_rejects_invalid_terminal_transition(tmp_path) -> None:
    store = FileRunStore(tmp_path)
    store.create_run(make_run())
    store.update_run("owner-a", "run-1", status="running")
    completed = store.update_run("owner-a", "run-1", status="succeeded")

    assert completed.status == "succeeded"
    with pytest.raises(InvalidRunTransition):
        store.update_run("owner-a", "run-1", status="running")


def test_file_run_store_keeps_partial_and_failed_as_terminal_states(tmp_path) -> None:
    store = FileRunStore(tmp_path)
    store.create_run(make_run("run-partial"))
    store.create_run(make_run("run-failed"))

    store.update_run("owner-a", "run-partial", status="running")
    partial = store.update_run("owner-a", "run-partial", status="partial")
    store.update_run("owner-a", "run-failed", status="failed")

    assert partial.status == "partial"
    with pytest.raises(InvalidRunTransition):
        store.update_run("owner-a", "run-partial", status="failed")
    with pytest.raises(InvalidRunTransition):
        store.update_run("owner-a", "run-failed", status="running")


def test_file_run_store_rejects_updates_for_another_owner(tmp_path) -> None:
    store = FileRunStore(tmp_path)
    store.create_run(make_run())

    with pytest.raises(RunNotFoundError):
        store.update_run("owner-b", "run-1", status="running")

    assert store.get_run("owner-a", "run-1").status == "queued"  # type: ignore[union-attr]


def test_file_run_store_lists_newest_runs_first(tmp_path) -> None:
    store = FileRunStore(tmp_path)
    base = datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
    store.create_run(make_run("run-old", created_at=base))
    store.create_run(make_run("run-new", created_at=base + timedelta(minutes=1)))

    assert [record.run_id for record in store.list_runs("owner-a")] == [
        "run-new",
        "run-old",
    ]


def test_file_run_store_cleans_only_expired_runs(tmp_path) -> None:
    store = FileRunStore(tmp_path)
    now = datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
    store.create_run(
        make_run(
            "run-expired",
            created_at=now - timedelta(days=8),
            retention_deadline=now - timedelta(seconds=1),
        )
    )
    store.create_run(
        make_run(
            "run-live",
            created_at=now - timedelta(days=1),
            retention_deadline=now + timedelta(days=1),
        )
    )

    deleted = store.cleanup_expired(now=now)

    assert deleted == 1
    assert store.get_run("owner-a", "run-expired") is None
    assert store.get_run("owner-a", "run-live") is not None


def test_run_record_rejects_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        RunRecord(
            run_id="run-1",
            owner_id="owner-a",
            status="queued",
            data_mode="demo",
            created_at=datetime(2026, 9, 21, 8, 0),
            updated_at=datetime(2026, 9, 21, 8, 0),
            retention_deadline=datetime(2026, 9, 28, 8, 0),
        )


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL")
    or not importlib.util.find_spec("psycopg"),
    reason="TEST_DATABASE_URL and psycopg are required for PostgreSQL integration tests",
)
def test_postgres_run_store_round_trip_and_owner_isolation(tmp_path) -> None:
    from web_task_agent.run_store import PostgresRunStore

    store = PostgresRunStore(os.environ["TEST_DATABASE_URL"])
    store.initialize()
    store.create_run(make_run("run-pg-1", "owner-pg-a"))
    store.create_run(make_run("run-pg-2", "owner-pg-b"))

    assert store.get_run("owner-pg-a", "run-pg-1") is not None
    assert store.get_run("owner-pg-a", "run-pg-2") is None
    assert [record.run_id for record in store.list_runs("owner-pg-a")] == ["run-pg-1"]
