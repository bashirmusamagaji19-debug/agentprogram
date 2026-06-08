from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _unique_clean_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        item = value.strip()
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            cleaned.append(item)
    return cleaned


class UserProfile(BaseModel):
    keyword: str
    location: str = "Remote"
    target_count: int = Field(default=10, ge=1, le=50)
    skills: list[str] = Field(default_factory=list)
    resume_text: str = ""

    @field_validator("keyword", "location")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value

    @field_validator("skills")
    @classmethod
    def normalize_skills(cls, values: list[str]) -> list[str]:
        return _unique_clean_strings(values)


class BrowserPage(BaseModel):
    url: str
    title: str = ""
    content: str
    source: str = "browser"
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobPosting(BaseModel):
    title: str
    company: str
    location: str
    source: str
    url: str
    requirements: str = ""
    responsibilities: str = ""
    skills: list[str] = Field(default_factory=list)
    posted_at: str = ""
    confidence: float = 0.0

    @field_validator("title", "company", "location", "source", "url")
    @classmethod
    def strip_core_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("core job fields must not be empty")
        return value

    @field_validator("skills")
    @classmethod
    def normalize_skills(cls, values: list[str]) -> list[str]:
        return _unique_clean_strings(values)

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("confidence must be finite")
        return min(max(value, 0.0), 1.0)


class MatchResult(BaseModel):
    job_id: str
    score: float = Field(ge=0.0, le=1.0)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    reason: str = ""
    priority: str = "medium"
    suggested_actions: list[str] = Field(default_factory=list)


class RunMetrics(BaseModel):
    run_id: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    pages_visited: int = Field(default=0, ge=0)
    jobs_found: int = Field(default=0, ge=0)
    valid_jobs: int = Field(default=0, ge=0)
    duplicate_jobs: int = Field(default=0, ge=0)
    failed_pages: int = Field(default=0, ge=0)
    avg_steps_per_job: float = Field(default=0.0, ge=0.0)
    estimated_token_cost: float = Field(default=0.0, ge=0.0)

    @field_validator("started_at", "finished_at")
    @classmethod
    def normalize_datetime_to_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("avg_steps_per_job", "estimated_token_cost")
    @classmethod
    def reject_non_finite_float_metrics(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("metric float must be finite")
        return value


class WorkflowState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user: UserProfile
    search_queries: list[str] = Field(default_factory=list)
    candidate_urls: list[str] = Field(default_factory=list)
    pages: list[BrowserPage] = Field(default_factory=list)
    jobs: list[JobPosting] = Field(default_factory=list)
    matches: list[MatchResult] = Field(default_factory=list)
    failed_urls: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    metrics: RunMetrics | None = None
    report_path: str | None = None
