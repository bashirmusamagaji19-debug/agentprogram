"""Private run recovery metadata and OS-backed single-run mutation locks."""
from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from web_task_agent.models import UserProfile

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class RunContext(BaseModel):
    schema_version: int = 1
    run_id: str
    owner_id: str
    thread_id: str
    repository_path: str
    checkpoint_path: str
    user: UserProfile
    options: dict[str, Any]
    started_at: datetime
    expires_at: datetime = Field(default_factory=lambda: datetime.now(UTC) + timedelta(days=7))
    status: str = "running"
    approval_id: str = ""
    payload_hash: str = ""
    resolved_outcome: str = ""
    source_manifest: str | None = None
    candidate_ids: list[str] = Field(default_factory=list)
    source_mode: str = ""
    require_verified_links: bool | None = None


def require_run_identity(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError("invalid run identity")
    return value


def write_run_context(root: Path, context: RunContext) -> None:
    root.mkdir(parents=True, exist_ok=True)
    target = root / "run-context.json"
    temporary = root / f".run-context-{uuid4().hex}.tmp"
    temporary.write_text(context.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)


def read_run_context(root: Path, *, run_id: str, owner_id: str) -> RunContext:
    require_run_identity(run_id)
    path = root / "run-context.json"
    context = RunContext.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if context.schema_version != 1:
        raise ValueError("unsupported run context schema")
    if context.owner_id != owner_id or context.run_id != run_id:
        raise PermissionError("run does not belong to this owner")
    if context.expires_at.tzinfo is None or context.expires_at <= datetime.now(UTC):
        raise ValueError("run context has expired")
    return context


@contextmanager
def lock_run(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    key = str(root.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise RuntimeError("run is already being processed")
    handle = None
    locked = False
    try:
        handle = (root / ".run.lock").open("a+b")
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    finally:
        if handle is not None:
            if locked:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        lock.release()
