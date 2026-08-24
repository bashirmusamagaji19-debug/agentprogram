from __future__ import annotations

import re
from datetime import UTC, datetime
from math import isfinite
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def _clean_strings(values: list[str]) -> list[str]:
    """Trim values and keep first occurrence case-insensitively."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _clean_text(value: str) -> str:
    return value.strip()


def _require_http_url(value: str, field_name: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an http or https URL")
    return value


class SearchIntent(BaseModel):
    raw_text: str
    role_keywords: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    excluded_roles: list[str] = Field(default_factory=list)
    target_count: int = Field(default=10, ge=1, le=20)

    _trim_raw_text = field_validator("raw_text")(classmethod(lambda cls, value: _clean_text(value)))

    @field_validator("raw_text")
    @classmethod
    def require_raw_text(cls, value: str) -> str:
        if not value:
            raise ValueError("raw_text must not be empty")
        return value
    _normalize_lists = field_validator(
        "role_keywords",
        "locations",
        "employment_types",
        "required_skills",
        "preferred_skills",
        "excluded_roles",
    )(classmethod(lambda cls, values: _clean_strings(values)))


class SearchCandidate(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""
    source: str = ""
    tags: list[str] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    content_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    _trim_text = field_validator("url", "title", "snippet", "source")(
        classmethod(lambda cls, value: _clean_text(value))
    )
    _normalize_tags = field_validator("tags")(
        classmethod(lambda cls, values: _clean_strings(values))
    )
    _trim_hash = field_validator("content_hash")(classmethod(lambda cls, value: value.strip()))

    @field_validator("content_hash")
    @classmethod
    def validate_optional_hash(cls, value: str) -> str:
        if value and not _HEX_RE.fullmatch(value):
            raise ValueError("content_hash must be hexadecimal when provided")
        return value.lower()

    @field_validator("url")
    @classmethod
    def require_url(cls, value: str) -> str:
        if not value:
            raise ValueError("url must not be empty")
        return _require_http_url(value, "url")

    @field_validator("score")
    @classmethod
    def require_finite_score(cls, value: float | None) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("score must be finite")
        return value


class FieldEvidence(BaseModel):
    field_name: str
    value: str = ""
    snippet: str = ""
    page_url: str
    content_hash: str

    _trim_text = field_validator("field_name", "value", "snippet", "page_url")(
        classmethod(lambda cls, value: _clean_text(value))
    )

    @field_validator("field_name", "page_url")
    @classmethod
    def require_identity_text(cls, value: str) -> str:
        if not value:
            raise ValueError("evidence identity fields must not be empty")
        return value

    @field_validator("page_url")
    @classmethod
    def require_page_url(cls, value: str) -> str:
        return _require_http_url(value, "page_url")

    @field_validator("content_hash")
    @classmethod
    def require_hex_hash(cls, value: str) -> str:
        value = value.strip()
        if not value or not _HEX_RE.fullmatch(value):
            raise ValueError("content_hash must be a non-empty hexadecimal string")
        return value.lower()


class VerifiedJob(BaseModel):
    title: str
    company: str
    location: str
    url: str
    source: str
    employment_type: str = ""
    responsibilities: str = ""
    requirements: str = ""
    description: str = ""
    skills: list[str] = Field(default_factory=list)
    evidence: list[FieldEvidence] = Field(default_factory=list, validate_default=True)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _trim_text = field_validator(
        "title",
        "company",
        "location",
        "url",
        "source",
        "employment_type",
        "responsibilities",
        "requirements",
        "description",
    )(classmethod(lambda cls, value: _clean_text(value)))
    _normalize_skills = field_validator("skills")(
        classmethod(lambda cls, values: _clean_strings(values))
    )

    @field_validator("title", "company", "location", "url", "source")
    @classmethod
    def require_core_text(cls, value: str) -> str:
        if not value:
            raise ValueError("verified job core fields must not be empty")
        return value

    @field_validator("evidence")
    @classmethod
    def dedupe_evidence(cls, values: list[FieldEvidence]) -> list[FieldEvidence]:
        if not values:
            raise ValueError("verified job requires at least one evidence item")
        seen: set[tuple[str, str, str]] = set()
        result: list[FieldEvidence] = []
        for evidence in values:
            key = (evidence.field_name.casefold(), evidence.page_url, evidence.content_hash)
            if key not in seen:
                seen.add(key)
                result.append(evidence)
        return result


class FailureRecord(BaseModel):
    code: str
    url: str = ""
    message: str = ""
    stage: str = ""
    recoverable: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    _trim_text = field_validator("code", "url", "message", "stage")(
        classmethod(lambda cls, value: _clean_text(value))
    )

    @field_validator("code")
    @classmethod
    def require_code_and_url(cls, value: str) -> str:
        if not value:
            raise ValueError("failure code and url must not be empty")
        return value


class SearchRunSummary(BaseModel):
    run_id: str
    target_count: int = Field(ge=1, le=20)
    candidates_seen: int = Field(default=0, ge=0)
    verified_count: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    terminal_reason: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    _trim_text = field_validator("run_id", "terminal_reason")(
        classmethod(lambda cls, value: _clean_text(value))
    )

    @field_validator("run_id", "terminal_reason")
    @classmethod
    def require_summary_text(cls, value: str) -> str:
        if not value:
            raise ValueError("summary identity fields must not be empty")
        return value
