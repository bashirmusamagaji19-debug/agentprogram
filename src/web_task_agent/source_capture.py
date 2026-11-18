"""Browser boundary that archives public candidates, bodies and every resolution attempt."""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from web_task_agent.job_sources import DiscoveredJob
from web_task_agent.models import BrowserPage
from web_task_agent.page_reliability import PageFailureCategory, classify_page_exception
from web_task_agent.source_archive import SourceArchive
from web_task_agent.source_provenance import ArchiveManifest, CandidateEvidence, candidate_id, candidate_record_sha256


def code_identity() -> tuple[str, str]:
    """Fingerprint public implementation files only; credentials and runtime data are excluded."""
    root = Path(__file__).resolve().parents[2]
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                              text=True, check=False, timeout=5).stdout.strip() or "unversioned"
    records = []
    for directory in ("src", "tests", "scripts"):
        for path in sorted((root / directory).rglob("*.py")):
            records.append((path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    for name in ("pyproject.toml", "requirements.txt"):
        path = root / name
        if path.is_file():
            records.append((name, hashlib.sha256(path.read_bytes()).hexdigest()))
    digest = hashlib.sha256(json.dumps(records, separators=(",", ":")).encode()).hexdigest()
    return revision, digest


class SourceCaptureBrowser:
    def __init__(self, browser, *, root: str | Path, run_id: str, keyword: str, location: str,
                 target_count: int, jobs: list[DiscoveredJob] | None = None,
                 seed_urls: list[str] | None = None, mode: str = "capture"):
        root = Path(root)
        if root.exists():
            raise FileExistsError("source capture cannot overwrite an existing archive")
        self.browser = browser
        self.archive = SourceArchive(root)
        self.run_id = run_id
        self.keyword, self.location = keyword, location
        self.conditions = {"keyword": keyword, "location": location, "target_count": target_count, "mode": mode}
        self.created_at = datetime.now(UTC)
        self.code_revision, self.dirty_hash = code_identity()
        self.candidates: dict[str, CandidateEvidence] = {}
        self._by_url: dict[str, list[CandidateEvidence]] = {}
        self._capture_lock = asyncio.Lock()
        self._operation_sequence = 0
        self.snapshot_ids: list[str] = []
        for job in jobs or []:
            self._register(job)
        for url in seed_urls or []:
            if url not in self._by_url:
                self._register(DiscoveredJob(url=url, source="seed_urls"))

    @property
    def discovered_count(self):
        return getattr(self.browser, "discovered_count", 0)

    @discovered_count.setter
    def discovered_count(self, value):
        self.browser.discovered_count = value

    @property
    def selected_count(self):
        return getattr(self.browser, "selected_count", len([
            url for url, rows in self._by_url.items()
            if any(c.query_context.get("type") != "search_page" for c in rows)]))

    @property
    def candidate_summaries(self):
        return {**getattr(self.browser, "candidate_summaries", {}), **{url: {"title": rows[0].title,
                      "company": rows[0].company,
                      "location": rows[0].location_hint, "source_spec": rows[0].source_spec}
                for url, all_rows in self._by_url.items()
                if (rows := [c for c in all_rows if c.query_context.get("type") != "search_page"])}}

    @property
    def source_diagnostics(self):
        return getattr(self.browser, "source_diagnostics", [])

    @property
    def resolution_log(self):
        if hasattr(self.browser, "resolution_log"):
            return self.browser.resolution_log
        loader = getattr(self.browser, "_page_loader", None)
        return getattr(loader, "resolution_log", [])

    def _register(self, job: DiscoveredJob) -> CandidateEvidence:
        identity = candidate_id(job.source, source_job_id=job.source_job_id, requested_url=job.url)
        existing = next((record for record in self._by_url.get(job.url, []) if record.candidate_id == identity), None)
        if existing:
            return existing
        record = CandidateEvidence(
            candidate_id=identity,
            source_spec=job.source, source_job_id=job.source_job_id, discovery_url=job.discovery_url,
            requested_url=job.url, keyword=self.keyword, location=self.location,
            discovered_at=job.discovered_at or datetime.now(UTC), title=job.title,
            company=job.company, location_hint=job.location, query_context=job.query_context,
        )
        self.archive.write_candidate(record)
        self.candidates[record.candidate_id] = record
        self._by_url.setdefault(job.url, []).append(record)
        return record

    def _operation_id(self, kind):
        self._operation_sequence += 1
        return f"{kind}-{self._operation_sequence:06d}"

    async def search(self, query: str, target_count: int):
        async with self._capture_lock:
            started_at, started = datetime.now(UTC), perf_counter()
            operation = self._operation_id("search")
            try:
                pages = await self.browser.search(query, target_count=target_count)
            except Exception as exc:
                discovery = self._register(DiscoveredJob(
                    url=getattr(self.browser, "search_endpoint", "https://www.google.com/search"),
                    source="browser-search-page", query_context={"type": "search_page", "query": query}))
                self.archive.append_attempt({
                    "candidate_id": discovery.candidate_id, "strategy": "search", "started_at": started_at,
                    "elapsed_ms": (perf_counter() - started) * 1000, "status": "error",
                    "error_category": classify_page_exception(exc).category,
                    "operation_id": operation, "operation_kind": "search"})
                raise
            for page in pages:
                discovery = self._register(DiscoveredJob(
                    url=page.url, title=page.title, source="browser-search-page",
                    query_context={"type": "search_page", "query": query},
                ))
                snapshot = self.archive.write_page(discovery.candidate_id, page, {
                    "captured_at": datetime.now(UTC), "observed_at": started_at,
                    "content_origin": "http", "evidence_level": "list", "availability": "observed",
                })
                if snapshot not in self.snapshot_ids:
                    self.snapshot_ids.append(snapshot)
                self.archive.append_attempt({"candidate_id": discovery.candidate_id, "strategy": "search",
                    "started_at": started_at, "elapsed_ms": (perf_counter() - started) * 1000,
                    "status": "success", "snapshot_id": snapshot,
                    "operation_id": operation, "operation_kind": "search"})
                urls = page.metadata.get("candidate_urls", [page.url])
                for url in urls:
                    if not any(c.query_context.get("type") != "search_page" for c in self._by_url.get(url, [])):
                        self._register(DiscoveredJob(url=url, source="browser-search", discovery_url=page.url,
                            query_context={"type": "discovered_url", "source": snapshot},
                            title=page.title if url == page.url else
                            getattr(self.browser, "candidate_summaries", {}).get(url, {}).get("title", "")))
            return pages

    async def open_url(self, url: str) -> BrowserPage:
        async with self._capture_lock:
            return await self._open_url(url)

    async def _open_url(self, url: str) -> BrowserPage:
        rows = [c for c in self._by_url.get(url, []) if c.query_context.get("type") != "search_page"]
        record = rows[0] if rows else self._register(DiscoveredJob(url=url, source="seed_urls"))
        operation = self._operation_id("open")
        started_at, started = datetime.now(UTC), perf_counter()
        offset = len(self.resolution_log)
        try:
            page = await self.browser.open_url(url)
        except Exception as error:
            events = [event for event in self.resolution_log[offset:] if event.get("url") == url]
            for bound_record in rows or [record]:
                self._write_events(bound_record, events, operation=operation)
                if not events or not events[-1].get("category"):
                    self.archive.append_attempt({"candidate_id": bound_record.candidate_id, "strategy": "browser",
                        "started_at": started_at, "elapsed_ms": (perf_counter() - started) * 1000,
                        "status": "error", "error_category": classify_page_exception(error).category,
                        "http_status": classify_page_exception(error).http_status,
                        "operation_id": operation, "operation_kind": "open"})
            raise
        events = [event for event in self.resolution_log[offset:] if event.get("url") == url]
        now = datetime.now(UTC)
        metadata = {**page.metadata, "candidate_record_sha256": candidate_record_sha256(record)}
        if len(rows) > 1:
            metadata["candidate_binding"] = "shared_page_requires_review"
        page = page.model_copy(update={"metadata": metadata})
        cached = bool(metadata.get("cache_hit")) or bool(events and events[-1]["strategy"] == "cache")
        origin = str(metadata.get("content_origin") or page.source)
        origin = "cache" if cached else ("official-api" if origin == "official-api" else
                    "list" if origin in {"list", "jd_text-fallback", "detail-probed"} or origin.startswith("aggregator")
                    else "http")
        observed = metadata.get("observed_at")
        observed = datetime.fromisoformat(observed) if isinstance(observed, str) else observed
        if cached and observed is None:
            self._write_events(record, events, legacy=True, operation=operation)
            return page.model_copy(update={"metadata": {**metadata, "provenance_state": "legacy_unknown"}})
        observed = observed or now
        if not events:
            events = [{"strategy": "browser", "started_at": started_at,
                       "elapsed_ms": (perf_counter() - started) * 1000}]
        for bound_record in rows or [record]:
            bound_page = page.model_copy(update={"metadata": {
                **metadata, "candidate_record_sha256": candidate_record_sha256(bound_record)}})
            snapshot = self.archive.write_page(bound_record.candidate_id, bound_page, {
                "captured_at": now, "observed_at": observed, "content_origin": origin,
                "canonical_url": metadata.get("canonical_url"),
                "evidence_level": "historical" if cached else "list" if origin == "list" else "detail",
                "availability": "observed" if origin in {"http", "official-api"} else "unknown",
                "cache_age_seconds": (now - observed).total_seconds() if cached else None,
            })
            if snapshot not in self.snapshot_ids:
                self.snapshot_ids.append(snapshot)
            self._write_events(bound_record, events, snapshot_id=snapshot, operation=operation)
        archived = self.archive.read_page(snapshot)
        result = page.model_copy(update={"metadata": {**metadata, **archived.metadata,
                                 "candidate_ids": [c.candidate_id for c in rows] or [record.candidate_id]}})
        attach = getattr(getattr(self.browser, "_page_loader", None), "attach_archive_references", None)
        if callable(attach) and not cached:
            attach(result)
        return result

    def _write_events(self, record, events, *, snapshot_id=None, legacy=False, operation):
        for index, event in enumerate(events):
            strategy = str(event["strategy"])
            failed = event.get("category") or any(word in strategy for word in ("too-short", "empty-page", "failed"))
            status = (
                "error"
                if failed
                else "unsupported"
                if "unsupported" in strategy
                else "miss"
                if ":miss" in strategy
                else "success"
            )
            if legacy and index == len(events) - 1:
                status = "legacy_unknown"
            started_at = event.get("started_at", datetime.now(UTC))
            if isinstance(started_at, str):
                started_at = datetime.fromisoformat(started_at)
            self.archive.append_attempt({
                "candidate_id": record.candidate_id, "strategy": strategy, "started_at": started_at,
                "elapsed_ms": event.get("elapsed_ms", 0), "status": status,
                "http_status": event.get("http_status"), "operation_id": operation, "operation_kind": "open",
                "error_category": PageFailureCategory(event.get("category") or "empty_shell") if failed else None,
                "snapshot_id": snapshot_id if index == len(events) - 1 and status == "success" else None,
            })

    def finalize(self) -> Path:
        self.archive.write_manifest(ArchiveManifest(
            run_id=self.run_id, conditions=self.conditions,
            candidate_ids=list(dict.fromkeys(c.candidate_id for c in self.candidates.values())),
            snapshot_ids=self.snapshot_ids, attempts_path="attempts.jsonl", created_at=self.created_at,
            code_revision=self.code_revision, dirty_manifest_sha256=self.dirty_hash,
        ))
        return self.archive.root / "manifest.json"

    def finalize_metadata(self, run_dir: Path) -> dict:
        self.finalize()
        return self.reference_metadata(run_dir)

    def reference_metadata(self, run_dir: Path) -> dict:
        path = self.archive.root / "manifest.json"
        local = path.is_relative_to(run_dir)
        reference = path.relative_to(run_dir).as_posix() if local else path.resolve().as_posix()
        return {"source_manifest": reference, "source_archive_scope": "run" if local else "external",
                "source_mode": self.conditions["mode"], "source_captured_at": self.created_at.isoformat()}
