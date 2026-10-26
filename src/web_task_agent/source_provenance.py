"""Strict, immutable source evidence and replay identifiers.

The models in this module contain public provenance only. Request headers,
cookies, credentials, response bodies, and exception messages do not belong in
the evidence records.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import unquote, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from web_task_agent.page_reliability import PageFailureCategory

_STRICT_FROZEN = ConfigDict(frozen=True, extra="forbid", strict=True)
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")
_SAFE_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DERIVED_CANDIDATE_ID = re.compile(r"candidate_[0-9a-f]{64}\Z")
_SOURCE_CONTEXT_KEYS = frozenset(
    {
        "candidate_binding",
        "canonical_url",
        "content_origin",
        "discovered_company",
        "discovered_location",
        "discovered_title",
        "page_source",
        "source_job_id",
        "source_spec",
        "tier",
        "internship_evidence_source",
        "internship_evidence_value",
    }
)
SOURCE_CONTEXT_KEYS = _SOURCE_CONTEXT_KEYS


# Query/context data is retained only when it describes a public job search.
# Headers, cookies and signatures are intentionally absent.
_PUBLIC_CONTEXT_KEYS = frozenset(
    {
        "candidate_id",
        "category",
        "city",
        "company",
        "conditions",
        "country",
        "degree",
        "department",
        "experience",
        "job_family",
        "job_id",
        "keyword",
        "keywords",
        "lang",
        "language",
        "limit",
        "location",
        "locations",
        "mode",
        "offset",
        "order",
        "page",
        "page_no",
        "page_number",
        "page_size",
        "position_id",
        "post_id",
        "q",
        "query",
        "recruit_type",
        "region",
        "sort",
        "source",
        "source_job_id",
        "source_spec",
        "target_count",
        "type",
    }
)
_CREDENTIAL_QUERY_KEYS = frozenset(
    {
        "apikey",
        "accesskey",
        "auth",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "key",
        "jwt",
        "password",
        "passwd",
        "session",
        "sessionid",
        "signature",
        "sig",
        "token",
        "ticket",
    }
)


def sha256_text(value: str) -> str:
    """Hash UTF-8 bytes exactly as stored, without newline normalization."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_sha256(value: str, field_name: str = "sha256") -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be 64 lowercase hexadecimal characters")
    return value


def validate_safe_id(value: str, field_name: str = "id") -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ValueError(f"{field_name} is not a safe identifier")
    value = value.strip()
    if not value or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{field_name} is not a safe identifier")
    return value


def validate_safe_relative_path(value: str) -> str:
    if value != value.strip():
        raise ValueError("path must be a safe relative path")
    if (
        not value
        or "\\" in value
        or ":" in value
        or value.startswith("/")
        or _CONTROL.search(value)
        or PurePosixPath(value).is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("path must be a safe relative path")
    return value


def _normalized_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _normalized_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    rendered = value.astimezone(UTC).isoformat(timespec="microseconds")
    return rendered.replace(".000000+00:00", "Z").replace("+00:00", "Z")


def _is_credential_query_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return (
        normalized in _CREDENTIAL_QUERY_KEYS
        or normalized.endswith("token")
        or normalized.endswith("secret")
        or normalized.endswith("password")
        or normalized.endswith("signature")
        or normalized.endswith("apikey")
        or normalized.startswith("xamz")
    )


def validate_public_url(value: str, field_name: str = "url") -> str:
    """Validate and normalize an HTTP(S) URL without request credentials."""

    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    value = value.strip()
    if not value or len(value) > 4096 or _CONTROL.search(value) or re.search(r"\s", value):
        raise ValueError(f"{field_name} must be a compact public URL")
    parts = urlsplit(value)
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"{field_name} must not contain URL credentials")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"{field_name} has an invalid port") from exc
    decoded_query = parts.query
    for _ in range(2):
        decoded_query = unquote(decoded_query)
    for fragment in re.split(r"[&;]", decoded_query):
        key = fragment.partition("=")[0]
        if _is_credential_query_key(key):
            raise ValueError(f"{field_name} must not contain query credentials")

    host = (parts.hostname or "").casefold()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    path = parts.path or "/"
    return urlunsplit((parts.scheme.casefold(), netloc, path, parts.query, ""))


def _public_text(value: str, field_name: str, *, required: bool, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if required and not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    if len(value) > maximum or _CONTROL.search(value):
        raise ValueError(f"{field_name} contains unsupported control data or is too long")
    return value.strip() if required else value


def _validate_public_context(value: dict[str, str | int], field_name: str) -> dict[str, str | int]:
    if len(value) > 50:
        raise ValueError(f"{field_name} has too many fields")
    clean: dict[str, str | int] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not _SAFE_KEY.fullmatch(key) or key not in _PUBLIC_CONTEXT_KEYS:
            raise ValueError(f"{field_name} key is not a public search field: {key!r}")
        if _is_credential_query_key(key):
            raise ValueError(f"{field_name} must not contain credentials")
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            raise ValueError(f"{field_name} values must be public strings or integers")
        if isinstance(item, str):
            _public_text(item, f"{field_name}.{key}", required=False, maximum=500)
        clean[key] = item
    return clean


def _validate_condition_value(value: str | int | list[str], field_name: str) -> str | int | list[str]:
    if isinstance(value, bool) or not isinstance(value, (str, int, list)):
        raise ValueError(f"{field_name} contains an unsupported condition value")
    if isinstance(value, str):
        return _public_text(value, field_name, required=False, maximum=500)
    if isinstance(value, int):
        return value
    if len(value) > 50 or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of public strings")
    return [_public_text(item, field_name, required=False, maximum=500) for item in value]


def normalize_conditions(
    value: Mapping[str, str | int | list[str]],
) -> dict[str, str | int | list[str]]:
    """Validate and copy public search conditions for comparison or replay."""

    if not isinstance(value, Mapping):
        raise ValueError("conditions must be a mapping")
    if len(value) > 50:
        raise ValueError("conditions has too many fields")
    clean: dict[str, str | int | list[str]] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not _SAFE_KEY.fullmatch(key) or key not in _PUBLIC_CONTEXT_KEYS:
            raise ValueError(f"conditions key is not a public search field: {key!r}")
        clean[key] = _validate_condition_value(item, f"conditions.{key}")
    return clean


def normalize_source_context(value: Mapping[str, str | int] | None) -> dict[str, str | int]:
    """Validate the public metadata needed to reproduce page extraction."""

    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("source_context must be a mapping")
    clean: dict[str, str | int] = {}
    for key, item in value.items():
        if key not in _SOURCE_CONTEXT_KEYS:
            raise ValueError(f"source_context key is not public extraction metadata: {key!r}")
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            raise ValueError("source_context values must be public strings or integers")
        if isinstance(item, str):
            _public_text(item, f"source_context.{key}", required=False, maximum=2000)
            if key == "canonical_url" and item:
                item = validate_public_url(item, "source_context.canonical_url")
        clean[key] = item
    return clean


def _source_context_digest(value: Mapping[str, str | int] | None) -> str:
    material = normalize_source_context(value)
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(encoded)


def source_context_sha256(value: Mapping[str, str | int] | None) -> str:
    return _source_context_digest(value)


def candidate_id(
    source_spec: str,
    *,
    source_job_id: str | None = None,
    canonical_url: str | None = None,
    requested_url: str | None = None,
) -> str:
    """Derive a stable ID from a native source ID, or a public URL fallback."""

    source_spec = _public_text(source_spec, "source_spec", required=True, maximum=200)
    if source_job_id is not None and source_job_id.strip():
        identity: dict[str, str] = {
            "kind": "source",
            "source_spec": source_spec,
            "source_job_id": _public_text(source_job_id, "source_job_id", required=True, maximum=300),
        }
    elif canonical_url or requested_url:
        identity = {
            "kind": "url",
            "source_spec": source_spec,
            "url": validate_public_url(canonical_url or requested_url or "", "candidate URL"),
        }
    else:
        raise ValueError("candidate_id requires a source_job_id or a URL")
    material = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "candidate_" + sha256_text(material)


def candidate_identity_sha256(
    candidate_id_value: str,
    *,
    source_spec: str,
    source_job_id: str | None,
    requested_url: str,
    canonical_url: str | None,
) -> str:
    """Bind an opaque candidate ID to its native source identity."""

    candidate_id_value = validate_safe_id(candidate_id_value, "candidate_id")
    source_spec = _public_text(source_spec, "source_spec", required=True, maximum=200)
    if source_job_id is not None and source_job_id.strip():
        identity: dict[str, str | None] = {
            "kind": "source",
            "candidate_id": candidate_id_value,
            "source_spec": source_spec,
            "source_job_id": _public_text(source_job_id, "source_job_id", required=True, maximum=300),
        }
    else:
        identity = {
            "kind": "url",
            "candidate_id": candidate_id_value,
            "source_spec": source_spec,
            "requested_url": validate_public_url(requested_url, "requested_url"),
            "canonical_url": (
                validate_public_url(canonical_url, "canonical_url") if canonical_url is not None else None
            ),
        }
    material = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(material)


def input_identity_hash(
    title: str,
    body: str,
    *,
    candidate_id: str,
    requested_url: str,
    canonical_url: str | None,
    content_origin: str,
    evidence_level: str,
    availability: str,
    captured_at: datetime,
    observed_at: datetime | None = None,
    cache_age_seconds: float | None = None,
    source_context: Mapping[str, str | int] | None = None,
    source_context_sha256: str | None = None,
    candidate_record_sha256: str | None = None,
) -> str:
    """Hash exact body plus every public snapshot metadata field."""

    if not isinstance(title, str) or not isinstance(body, str):
        raise TypeError("title and body must be text")
    if content_origin not in {"official-api", "http", "list", "cache"}:
        raise ValueError("content_origin is not a public snapshot source")
    if evidence_level not in {"detail", "list", "historical"}:
        raise ValueError("evidence_level is not a public snapshot level")
    if availability not in {"observed", "removed", "unknown"}:
        raise ValueError("availability is not a public snapshot state")
    captured_at = _normalized_aware(captured_at, "captured_at")
    if observed_at is not None:
        observed_at = _normalized_aware(observed_at, "observed_at")
    if cache_age_seconds is not None:
        if isinstance(cache_age_seconds, bool) or not isinstance(cache_age_seconds, (int, float)):
            raise ValueError("cache_age_seconds must be numeric")
        cache_age_seconds = float(cache_age_seconds)
        if not math.isfinite(cache_age_seconds) or cache_age_seconds < 0:
            raise ValueError("cache_age_seconds must be finite and nonnegative")
    if content_origin == "cache":
        if observed_at is None:
            raise ValueError("cache evidence requires original observed_at")
        if cache_age_seconds is None:
            raise ValueError("cache evidence requires cache_age_seconds")
        expected_age = (captured_at - observed_at).total_seconds()
        if not math.isclose(cache_age_seconds, expected_age, abs_tol=1.0):
            raise ValueError("cache_age_seconds must match captured_at minus observed_at")
    normalized_context = normalize_source_context(source_context)
    expected_context_hash = _source_context_digest(normalized_context)
    if source_context_sha256 is not None:
        validate_sha256(source_context_sha256, "source_context_sha256")
        if source_context_sha256 != expected_context_hash:
            raise ValueError("source_context_sha256 does not match source_context")
    if candidate_record_sha256 is not None:
        validate_sha256(candidate_record_sha256, "candidate_record_sha256")
    material = {
        "schema_version": 1,
        "candidate_id": validate_safe_id(candidate_id, "candidate_id"),
        "requested_url": validate_public_url(requested_url, "requested_url"),
        "canonical_url": (validate_public_url(canonical_url, "canonical_url") if canonical_url is not None else None),
        "title": title,
        "body": body,
        "content_origin": content_origin,
        "evidence_level": evidence_level,
        "availability": availability,
        "captured_at": _normalized_iso(captured_at),
        "observed_at": _normalized_iso(observed_at),
        "cache_age_seconds": cache_age_seconds,
        "source_context": normalized_context,
        "source_context_sha256": expected_context_hash,
        "candidate_record_sha256": candidate_record_sha256,
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(encoded)


def snapshot_identity_hash(input_sha256: str) -> str:
    validate_sha256(input_sha256, "input_sha256")
    return "snap_" + sha256_text("snapshot\0" + input_sha256)


class CandidateEvidence(BaseModel):
    model_config = _STRICT_FROZEN

    schema_version: Literal[1] = 1
    candidate_id: str
    source_spec: str
    source_job_id: str | None = None
    discovery_url: str | None = None
    requested_url: str
    canonical_url: str | None = None
    keyword: str
    location: str
    discovered_at: datetime
    title: str = ""
    company: str = ""
    location_hint: str = ""
    query_context: dict[str, str | int] = Field(default_factory=dict)
    identity_sha256: str | None = None

    @field_validator("candidate_id")
    @classmethod
    def valid_candidate_id(cls, value: str) -> str:
        return validate_safe_id(value, "candidate_id")

    @field_validator("source_spec")
    @classmethod
    def valid_source_spec(cls, value: str) -> str:
        return _public_text(value, "source_spec", required=True, maximum=200)

    @field_validator("source_job_id")
    @classmethod
    def valid_source_job_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _public_text(value, "source_job_id", required=True, maximum=300)

    @field_validator("discovery_url", "requested_url", "canonical_url")
    @classmethod
    def valid_urls(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return validate_public_url(value, info.field_name)

    @field_validator("keyword", "location")
    @classmethod
    def required_search_fields(cls, value: str, info) -> str:
        return _public_text(value, info.field_name, required=True, maximum=500)

    @field_validator("title", "company", "location_hint")
    @classmethod
    def optional_public_text(cls, value: str, info) -> str:
        return _public_text(value, info.field_name, required=False, maximum=2000)

    @field_validator("discovered_at")
    @classmethod
    def aware_discovered_at(cls, value: datetime) -> datetime:
        return _normalized_aware(value, "discovered_at")

    @field_validator("query_context")
    @classmethod
    def public_query_context(cls, value: dict[str, str | int]) -> dict[str, str | int]:
        return _validate_public_context(value, "query_context")

    @field_validator("identity_sha256")
    @classmethod
    def valid_identity_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_sha256(value, "identity_sha256")

    def model_post_init(self, __context: Any) -> None:
        expected_identity = candidate_identity_sha256(
            self.candidate_id,
            source_spec=self.source_spec,
            source_job_id=self.source_job_id,
            requested_url=self.requested_url,
            canonical_url=self.canonical_url,
        )
        if self.identity_sha256 is not None and self.identity_sha256 != expected_identity:
            raise ValueError("identity_sha256 does not match native source identity")
        object.__setattr__(self, "identity_sha256", expected_identity)
        if _DERIVED_CANDIDATE_ID.fullmatch(self.candidate_id):
            expected_id = candidate_id(
                self.source_spec,
                source_job_id=self.source_job_id,
                canonical_url=self.canonical_url,
                requested_url=self.requested_url,
            )
            if expected_id != self.candidate_id:
                raise ValueError("derived candidate_id does not match native source identity")


def candidate_record_sha256(
    evidence: CandidateEvidence | Mapping[str, Any],
) -> str:
    """Hash one exact candidate history version, including extraction context."""

    record = CandidateEvidence.model_validate(
        evidence.model_dump() if isinstance(evidence, CandidateEvidence) else evidence
    )
    material = json.dumps(
        record.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256_text(material)


class SnapshotEvidence(BaseModel):
    model_config = _STRICT_FROZEN

    schema_version: Literal[1] = 1
    snapshot_id: str
    candidate_id: str
    captured_at: datetime
    content_origin: Literal["official-api", "http", "list", "cache"]
    requested_url: str
    canonical_url: str | None = None
    title: str
    body_relative_path: str
    body_sha256: str
    input_sha256: str
    evidence_level: Literal["detail", "list", "historical"]
    availability: Literal["observed", "removed", "unknown"]
    observed_at: datetime | None = None
    cache_age_seconds: float | None = None
    source_context: dict[str, str | int] = Field(default_factory=dict)
    source_context_sha256: str | None = None
    candidate_record_sha256: str | None = None

    @field_validator("snapshot_id", "candidate_id")
    @classmethod
    def valid_ids(cls, value: str, info) -> str:
        return validate_safe_id(value, info.field_name)

    @field_validator("captured_at")
    @classmethod
    def aware_captured_at(cls, value: datetime) -> datetime:
        return _normalized_aware(value, "captured_at")

    @field_validator("requested_url", "canonical_url")
    @classmethod
    def valid_urls(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return validate_public_url(value, info.field_name)

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        return _public_text(value, "title", required=False, maximum=2000)

    @field_validator("body_relative_path")
    @classmethod
    def valid_body_path(cls, value: str) -> str:
        value = validate_safe_relative_path(value)
        path = PurePosixPath(value)
        if len(path.parts) != 2 or path.parts[0] != "bodies" or path.suffix != ".txt":
            raise ValueError("path must be a safe relative bodies/<sha256>.txt path")
        return value

    @field_validator("body_sha256")
    @classmethod
    def valid_body_hash(cls, value: str) -> str:
        return validate_sha256(value, "body_sha256")

    @field_validator("input_sha256")
    @classmethod
    def valid_input_hash(cls, value: str) -> str:
        return validate_sha256(value, "input_sha256")

    @field_validator("observed_at")
    @classmethod
    def aware_observed_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _normalized_aware(value, "observed_at")

    @field_validator("cache_age_seconds")
    @classmethod
    def valid_cache_age(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not math.isfinite(value) or value < 0:
            raise ValueError("cache_age_seconds must be finite and nonnegative")
        return value

    @field_validator("source_context")
    @classmethod
    def valid_source_context(cls, value: dict[str, str | int]) -> dict[str, str | int]:
        return normalize_source_context(value)

    @field_validator("source_context_sha256", "candidate_record_sha256")
    @classmethod
    def valid_context_hashes(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return validate_sha256(value, info.field_name)

    def model_post_init(self, __context: Any) -> None:
        if self.observed_at is not None and self.observed_at > self.captured_at:
            raise ValueError("observed_at must not be later than captured_at")
        if self.content_origin == "cache" and self.availability != "unknown":
            raise ValueError("cache evidence must have unknown current availability")
        if self.content_origin == "cache":
            if self.observed_at is None:
                raise ValueError("cache evidence requires original observed_at")
            if self.cache_age_seconds is None:
                raise ValueError("cache evidence requires cache_age_seconds")
            expected_age = (self.captured_at - self.observed_at).total_seconds()
            if not math.isclose(self.cache_age_seconds, expected_age, abs_tol=1.0):
                raise ValueError("cache_age_seconds must match captured_at minus observed_at")
        if self.body_relative_path != f"bodies/{self.body_sha256}.txt":
            raise ValueError("body_relative_path must name the declared body hash")
        expected_context_hash = _source_context_digest(self.source_context)
        if self.source_context_sha256 is not None and self.source_context_sha256 != expected_context_hash:
            raise ValueError("source_context_sha256 does not match source_context")
        object.__setattr__(self, "source_context_sha256", expected_context_hash)
        if self.snapshot_id != snapshot_identity_hash(self.input_sha256):
            raise ValueError("snapshot_id does not match input_sha256")


class ResolutionAttempt(BaseModel):
    model_config = _STRICT_FROZEN

    schema_version: Literal[1] = 1
    candidate_id: str
    sequence: int = Field(ge=1)
    strategy: str
    started_at: datetime
    elapsed_ms: float = Field(ge=0)
    status: str
    error_category: PageFailureCategory | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    snapshot_id: str | None = None
    operation_id: str | None = None
    operation_kind: Literal["open", "search"] | None = None

    @field_validator("operation_id")
    @classmethod
    def valid_operation_id(cls, value: str | None) -> str | None:
        return validate_safe_id(value, "operation_id") if value is not None else None

    @field_validator("candidate_id")
    @classmethod
    def valid_candidate_id(cls, value: str) -> str:
        return validate_safe_id(value, "candidate_id")

    @field_validator("strategy")
    @classmethod
    def valid_strategy(cls, value: str) -> str:
        return _public_text(value, "strategy", required=True, maximum=120)

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        return _public_text(value, "status", required=True, maximum=40)

    @field_validator("started_at")
    @classmethod
    def aware_started_at(cls, value: datetime) -> datetime:
        return _normalized_aware(value, "started_at")

    @field_validator("elapsed_ms")
    @classmethod
    def finite_elapsed(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("elapsed_ms must be finite")
        return value

    @field_validator("error_category", mode="before")
    @classmethod
    def parse_error_category(cls, value: object) -> object:
        if value is None or isinstance(value, PageFailureCategory):
            return value
        if isinstance(value, str):
            try:
                return PageFailureCategory(value)
            except ValueError as exc:
                raise ValueError("error_category is not a known page failure category") from exc
        return value

    @field_validator("snapshot_id")
    @classmethod
    def valid_snapshot_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_safe_id(value, "snapshot_id")

    def model_post_init(self, __context: Any) -> None:
        failure_status = self.status.casefold() in {"failure", "failed", "error"}
        success_status = self.status.casefold() in {"success", "succeeded", "ok"}
        if failure_status and self.error_category is None:
            raise ValueError("failed attempt requires error_category")
        if success_status and self.error_category is not None:
            raise ValueError("successful attempt cannot have error_category")


class ArchiveManifest(BaseModel):
    model_config = _STRICT_FROZEN

    schema_version: Literal[1] = 1
    run_id: str
    conditions: dict[str, str | int | list[str]]
    candidate_ids: list[str]
    snapshot_ids: list[str]
    attempts_path: str
    created_at: datetime
    code_revision: str
    dirty_manifest_sha256: str
    candidates_count: int | None = Field(default=None, ge=0)
    candidates_sha256: str | None = None
    attempts_count: int | None = Field(default=None, ge=0)
    attempts_sha256: str | None = None
    revision_sequence: int | None = Field(default=None, ge=1)
    revision_sha256: str | None = None

    @field_validator("run_id")
    @classmethod
    def valid_run_id(cls, value: str) -> str:
        return validate_safe_id(value, "run_id")

    @field_validator("conditions")
    @classmethod
    def public_conditions(cls, value: dict[str, str | int | list[str]]) -> dict[str, str | int | list[str]]:
        return normalize_conditions(value)

    @field_validator("candidate_ids", "snapshot_ids")
    @classmethod
    def unique_ids(cls, values: list[str], info) -> list[str]:
        checked = [validate_safe_id(value, info.field_name) for value in values]
        if len(checked) != len(set(checked)):
            raise ValueError(f"{info.field_name} must not contain duplicates")
        return checked

    @field_validator("attempts_path")
    @classmethod
    def valid_attempts_path(cls, value: str) -> str:
        return validate_safe_relative_path(value)

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        return _normalized_aware(value, "created_at")

    @field_validator("code_revision")
    @classmethod
    def valid_code_revision(cls, value: str) -> str:
        return _public_text(value, "code_revision", required=True, maximum=200)

    @field_validator("dirty_manifest_sha256")
    @classmethod
    def valid_dirty_hash(cls, value: str) -> str:
        return validate_sha256(value, "dirty_manifest_sha256")

    @field_validator("attempts_sha256")
    @classmethod
    def valid_attempts_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_sha256(value, "attempts_sha256")

    @field_validator("candidates_sha256")
    @classmethod
    def valid_candidates_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_sha256(value, "candidates_sha256")

    @field_validator("revision_sha256")
    @classmethod
    def valid_revision_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_sha256(value, "revision_sha256")


class ManifestRevision(BaseModel):
    """One immutable manifest revision and the ledger prefixes it observed."""

    model_config = _STRICT_FROZEN

    schema_version: Literal[1] = 1
    sequence: int = Field(ge=1)
    previous_revision_sha256: str | None = None
    run_id: str
    conditions: dict[str, str | int | list[str]]
    candidate_ids: list[str]
    snapshot_ids: list[str]
    attempts_path: str
    created_at: datetime
    code_revision: str
    dirty_manifest_sha256: str
    candidates_count: int = Field(ge=0)
    candidates_bytes: int = Field(ge=0)
    candidates_sha256: str
    attempts_count: int = Field(ge=0)
    attempts_bytes: int = Field(ge=0)
    attempts_sha256: str

    @field_validator("run_id")
    @classmethod
    def valid_run_id(cls, value: str) -> str:
        return validate_safe_id(value, "run_id")

    @field_validator("conditions")
    @classmethod
    def public_conditions(cls, value: dict[str, str | int | list[str]]) -> dict[str, str | int | list[str]]:
        return normalize_conditions(value)

    @field_validator("candidate_ids", "snapshot_ids")
    @classmethod
    def unique_ids(cls, values: list[str], info) -> list[str]:
        checked = [validate_safe_id(value, info.field_name) for value in values]
        if len(checked) != len(set(checked)):
            raise ValueError(f"{info.field_name} must not contain duplicates")
        return checked

    @field_validator("attempts_path")
    @classmethod
    def valid_attempts_path(cls, value: str) -> str:
        return validate_safe_relative_path(value)

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        return _normalized_aware(value, "created_at")

    @field_validator("code_revision")
    @classmethod
    def valid_code_revision(cls, value: str) -> str:
        return _public_text(value, "code_revision", required=True, maximum=200)

    @field_validator("dirty_manifest_sha256")
    @classmethod
    def valid_dirty_hash(cls, value: str) -> str:
        return validate_sha256(value, "dirty_manifest_sha256")

    @field_validator("candidates_sha256")
    @classmethod
    def valid_candidates_hash(cls, value: str) -> str:
        return validate_sha256(value, "candidates_sha256")

    @field_validator("attempts_sha256")
    @classmethod
    def valid_attempts_hash(cls, value: str) -> str:
        return validate_sha256(value, "attempts_sha256")

    @field_validator("previous_revision_sha256")
    @classmethod
    def valid_previous_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_sha256(value, "previous_revision_sha256")

    def model_post_init(self, __context: Any) -> None:
        if self.sequence == 1 and self.previous_revision_sha256 is not None:
            raise ValueError("first manifest revision cannot have a predecessor")
        if self.sequence > 1 and self.previous_revision_sha256 is None:
            raise ValueError("manifest revision requires previous_revision_sha256")


def manifest_revision_sha256(revision: ManifestRevision | Mapping[str, Any]) -> str:
    record = ManifestRevision.model_validate(
        revision.model_dump() if isinstance(revision, ManifestRevision) else revision
    )
    material = json.dumps(
        record.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256_text(material)
