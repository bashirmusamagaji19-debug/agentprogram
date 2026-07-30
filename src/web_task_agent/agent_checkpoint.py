from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from web_task_agent.agent_approval import (
    ApprovalAuditEvent,
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalStatus,
)
from web_task_agent.agent_models import (
    AgentAction,
    AgentBudget,
    AgentDecision,
    AgentMetrics,
    DecisionAgentState,
    DecisionSource,
    ToolObservation,
)
from web_task_agent.models import BrowserPage, JobPosting, MatchResult, UserProfile


def build_checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            ApprovalAuditEvent,
            ApprovalDecision,
            ApprovalOutcome,
            ApprovalRequest,
            ApprovalStatus,
            AgentAction,
            AgentBudget,
            AgentDecision,
            AgentMetrics,
            DecisionAgentState,
            DecisionSource,
            ToolObservation,
            BrowserPage,
            JobPosting,
            MatchResult,
            UserProfile,
        ]
    )


@asynccontextmanager
async def open_sqlite_checkpointer(
    db_path: str | Path,
) -> AsyncIterator[AsyncSqliteSaver]:
    path = Path(db_path)
    if path.name in {"", ".", ".."}:
        raise ValueError("checkpoint database path must name a file")
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(path)) as connection:
        saver = AsyncSqliteSaver(
            connection,
            serde=build_checkpoint_serializer(),
        )
        await saver.setup()
        yield saver
