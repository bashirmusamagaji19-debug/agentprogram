from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApprovalOutcome(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: str
    thread_id: str
    action: Literal["save_results"] = "save_results"
    requested_at: datetime
    job_count: int = Field(ge=0)
    summary: str = Field(min_length=1, max_length=200)
    status: ApprovalStatus = ApprovalStatus.PENDING

    @field_validator("approval_id", "thread_id")
    @classmethod
    def require_identifier(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("approval_id and thread_id must not be blank")
        return value

    @field_validator("requested_at")
    @classmethod
    def normalize_requested_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)

    def public_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: str
    outcome: ApprovalOutcome
    note: str = Field(default="", max_length=500)

    @field_validator("approval_id", "note")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("approval_id")
    @classmethod
    def require_approval_id(cls, value: str) -> str:
        if not value:
            raise ValueError("approval_id must not be blank")
        return value


class ApprovalAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: str
    event: Literal["requested", "resolved"]
    action: Literal["save_results"] = "save_results"
    occurred_at: datetime
    outcome: ApprovalOutcome | None = None
    note: str = Field(default="", max_length=500)

    @field_validator("approval_id", "note")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("approval_id")
    @classmethod
    def require_approval_id(cls, value: str) -> str:
        if not value:
            raise ValueError("approval_id must not be blank")
        return value

    @field_validator("occurred_at")
    @classmethod
    def normalize_occurred_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class HitlRunStatus(StrEnum):
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    PARTIAL = "partial"
    FAILED = "failed"


class HitlRuntimeError(RuntimeError):
    pass
