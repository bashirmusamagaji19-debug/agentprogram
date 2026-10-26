"""Public source-quality metrics derived from a verified source archive."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from web_task_agent.source_archive import SourceArchive, SourceArchiveError


def _ratio(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def _empty_summary() -> dict[str, Any]:
    return {
        "source_callable": _ratio(0, 0),
        "sources_with_candidates": _ratio(0, 0),
        "source_empty": 0,
        "source_error": 0,
        "detail_body_fetch": _ratio(0, 0),
        "deliveries_by_origin": {},
        "conditions_passed": _ratio(0, 0),
        "snapshot_integrity": _ratio(0, 0),
    }


def _diagnostic_summary(
    diagnostics: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, int | float | None], dict[str, int | float | None], int, int]:
    callable_count = 0
    with_candidates = 0
    empty_count = 0
    error_count = 0
    for item in diagnostics:
        status = str(item.get("status", "")).casefold()
        count = item.get("count")
        count_value = int(count) if isinstance(count, int) and not isinstance(count, bool) else 0
        if status in {"failed", "error", "unavailable"}:
            error_count += 1
        else:
            callable_count += 1
        if status == "empty":
            empty_count += 1
        if count_value > 0:
            with_candidates += 1
    denominator = len(diagnostics)
    return (
        _ratio(callable_count, denominator),
        _ratio(with_candidates, denominator),
        empty_count,
        error_count,
    )


def _snapshot_pages(archive: SourceArchive) -> tuple[list[Any], dict[str, list[Any]]]:
    manifest = archive.read_manifest()
    pages = [archive.read_page(snapshot_id) for snapshot_id in manifest.snapshot_ids]
    by_url: dict[str, list[Any]] = {}
    for page in pages:
        urls = {
            str(value)
            for value in (
                page.url,
                page.metadata.get("requested_url"),
                page.metadata.get("canonical_url"),
            )
            if value
        }
        for url in urls:
            by_url.setdefault(url, []).append(page)
    return pages, by_url


def _latest_page(pages: Sequence[Any]) -> Any | None:
    if not pages:
        return None
    return max(
        pages,
        key=lambda page: (
            str(page.metadata.get("captured_at") or ""),
            str(page.metadata.get("snapshot_id") or ""),
        ),
    )


def build_source_metrics(
    archive: SourceArchive | str | Path,
    *,
    source_diagnostics: Sequence[Mapping[str, Any]] = (),
    delivered_urls: Sequence[str] = (),
    verified_urls: Sequence[str] = (),
    verification_urls: Sequence[str] = (),
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Build separate source, evidence and gate metrics without merging units."""

    source_archive = archive if isinstance(archive, SourceArchive) else SourceArchive(archive)
    summary = _empty_summary()
    summary["source_callable"], summary["sources_with_candidates"], summary["source_empty"], summary["source_error"] = (
        _diagnostic_summary(source_diagnostics)
    )

    if not source_archive.manifest_path.is_file():
        return summary, {}

    try:
        pages, pages_by_url = _snapshot_pages(source_archive)
    except SourceArchiveError:
        summary["snapshot_integrity"] = _ratio(0, 1)
        return summary, {}

    unique_delivered = list(dict.fromkeys(str(url) for url in delivered_urls))
    delivered_pages = [
        page
        for url in unique_delivered
        if (page := _latest_page(pages_by_url.get(url, []))) is not None
    ]
    origin_counts = Counter(
        str(page.metadata.get("snapshot_content_origin") or page.metadata.get("content_origin") or "unknown")
        for page in delivered_pages
    )
    summary["deliveries_by_origin"] = dict(sorted(origin_counts.items()))
    cache_snapshots = {str(page.metadata.get("snapshot_id")) for page in pages
                       if page.metadata.get("snapshot_content_origin") == "cache"}
    attempted_candidates = {
        attempt.candidate_id for attempt in source_archive.read_attempts()
        if attempt.operation_kind == "open"
        and attempt.strategy not in {"cache", "cache:miss", "official-api:unsupported", "jd_text-fallback"}
        and not (attempt.snapshot_id in cache_snapshots
                 and not attempt.strategy.startswith(("http", "official-api")))
    }
    detail_candidates = {str(page.metadata.get("candidate_id")) for page in pages
                         if page.metadata.get("evidence_level") == "detail"}
    summary["detail_body_fetch"] = _ratio(len(detail_candidates & attempted_candidates), len(attempted_candidates))

    verification = list(dict.fromkeys(str(url) for url in verification_urls))
    verified = set(map(str, verified_urls))
    passed = sum(url in verified for url in verification)
    summary["conditions_passed"] = _ratio(passed, len(verification))

    snapshot_count = len(pages)
    summary["snapshot_integrity"] = _ratio(snapshot_count, snapshot_count)
    evidence: dict[str, dict[str, Any]] = {}
    for page in delivered_pages:
        metadata = page.metadata
        item = {
            "snapshot_id": metadata.get("snapshot_id"),
            "content_origin": metadata.get("content_origin") or metadata.get("snapshot_content_origin"),
            "acquisition_origin": metadata.get("snapshot_content_origin") or metadata.get("content_origin"),
            "evidence_level": metadata.get("evidence_level"),
            "availability": metadata.get("availability"),
            "observed_at": metadata.get("observed_at") or metadata.get("captured_at"),
            "source_spec": metadata.get("source_spec"),
        }
        for url in (page.url, metadata.get("requested_url"), metadata.get("canonical_url")):
            if url:
                evidence[str(url)] = item
    return summary, evidence
