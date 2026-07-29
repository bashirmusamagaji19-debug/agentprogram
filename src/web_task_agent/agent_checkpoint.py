from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


@asynccontextmanager
async def open_sqlite_checkpointer(
    db_path: str | Path,
) -> AsyncIterator[AsyncSqliteSaver]:
    path = Path(db_path)
    if path.name in {"", ".", ".."}:
        raise ValueError("checkpoint database path must name a file")
    path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        await saver.setup()
        yield saver
