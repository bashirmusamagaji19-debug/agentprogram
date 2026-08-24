from __future__ import annotations

import hashlib

from .models import FieldEvidence


def build_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_field_evidence(
    field_name: str,
    value: str,
    *,
    source_text: str,
    page_url: str = "https://example.com",
) -> FieldEvidence:
    return FieldEvidence(
        field_name=field_name,
        value=value.strip(),
        snippet=source_text.strip(),
        page_url=page_url,
        content_hash=build_content_hash(source_text),
    )


def extraction_incomplete(field_name: str, *, source_text: str, page_url: str) -> FieldEvidence:
    return build_field_evidence(field_name, "", source_text=source_text, page_url=page_url)
