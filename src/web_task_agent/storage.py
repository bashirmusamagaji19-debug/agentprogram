from __future__ import annotations

import json
import sqlite3
import threading
import weakref
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from web_task_agent.models import BrowserPage, JobPosting, RunMetrics


@dataclass(frozen=True)
class SaveReceipt:
    idempotency_key: str
    saved_jobs: int
    reused: bool


class JobRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._operation_lock = threading.RLock()
        self._memory_uri: str | None = None
        if str(db_path) == ":memory:":
            # Each repository owns one shared in-memory database. An anchor keeps
            # its tables alive while individual transactional connections close.
            self._memory_uri = f"file:web-task-agent-{uuid4().hex}?mode=memory&cache=shared"
            anchor = sqlite3.connect(self._memory_uri, uri=True, check_same_thread=False)
            self._memory_finalizer = weakref.finalize(self, anchor.close)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    url TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT NOT NULL,
                    source TEXT NOT NULL,
                    requirements TEXT NOT NULL,
                    responsibilities TEXT NOT NULL,
                    skills_json TEXT NOT NULL,
                    posted_at TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    link_verification_json TEXT NOT NULL DEFAULT '{}',
                    provenance_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            self._migrate_jobs(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_metrics (
                    run_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    pages_visited INTEGER NOT NULL,
                    jobs_found INTEGER NOT NULL,
                    valid_jobs INTEGER NOT NULL,
                    duplicate_jobs INTEGER NOT NULL,
                    failed_pages INTEGER NOT NULL,
                    avg_steps_per_job REAL NOT NULL,
                    estimated_token_cost REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS save_receipts (
                    idempotency_key TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    saved_jobs INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS page_cache (
                    url TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    canonical_url TEXT NOT NULL DEFAULT '',
                    content_origin TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._migrate_page_cache(conn)
            metric_columns = {row["name"] for row in conn.execute("PRAGMA table_info(run_metrics)")}
            if "cost_known" not in metric_columns:
                conn.execute("ALTER TABLE run_metrics ADD COLUMN cost_known INTEGER NOT NULL DEFAULT 0")
                conn.execute("UPDATE run_metrics SET cost_known=1 WHERE estimated_token_cost>0")

    def save_jobs(self, jobs: list[JobPosting]) -> None:
        with self._connection() as conn:
            self._save_jobs_with_connection(conn, jobs)

    def save_jobs_once(
        self,
        jobs: list[JobPosting],
        *,
        idempotency_key: str,
    ) -> SaveReceipt:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key must not be blank")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT saved_jobs FROM save_receipts WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if existing is not None:
                return SaveReceipt(key, int(existing["saved_jobs"]), True)
            self._save_jobs_with_connection(conn, jobs)
            conn.execute(
                """
                INSERT INTO save_receipts (idempotency_key, created_at, saved_jobs)
                VALUES (?, ?, ?)
                """,
                (key, datetime.now(UTC).isoformat(), len(jobs)),
            )
        return SaveReceipt(key, len(jobs), False)

    def list_jobs(self) -> list[JobPosting]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT title, company, location, source, url, requirements,
                       responsibilities, skills_json, posted_at, confidence,
                       link_verification_json, provenance_json
                FROM jobs
                ORDER BY company, title
                """
            ).fetchall()

        jobs: list[JobPosting] = []
        for row in rows:
            provenance = self._json_object(row["provenance_json"])
            job = JobPosting(
                title=row["title"],
                company=row["company"],
                location=row["location"],
                source=row["source"],
                url=row["url"],
                requirements=row["requirements"],
                responsibilities=row["responsibilities"],
                skills=json.loads(row["skills_json"]),
                posted_at=row["posted_at"],
                confidence=row["confidence"],
                tier=str(provenance.get("tier") or ""),
                category=str(provenance.get("category") or ""),
                url_verification=str(provenance.get("url_verification") or "unverified"),
                internship_evidence_source=str(
                    provenance.get("internship_evidence_source") or ""
                ),
                internship_evidence_value=str(
                    provenance.get("internship_evidence_value") or ""
                ),
            )
            jobs.append(
                job.model_copy(
                    update={
                        "link_verification": self._json_object(
                            row["link_verification_json"]
                        )
                    }
                )
            )
        return jobs

    def save_run_metrics(self, metrics: RunMetrics) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO run_metrics (
                    run_id, started_at, finished_at, pages_visited, jobs_found,
                    valid_jobs, duplicate_jobs, failed_pages, avg_steps_per_job,
                    estimated_token_cost, cost_known
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metrics.run_id,
                    metrics.started_at.isoformat(),
                    metrics.finished_at.isoformat() if metrics.finished_at else None,
                    metrics.pages_visited,
                    metrics.jobs_found,
                    metrics.valid_jobs,
                    metrics.duplicate_jobs,
                    metrics.failed_pages,
                    metrics.avg_steps_per_job,
                    metrics.estimated_token_cost if metrics.estimated_token_cost is not None else 0.0,
                    int(metrics.estimated_token_cost is not None),
                ),
            )

    def get_run_metrics(self, run_id: str) -> RunMetrics | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM run_metrics WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None

        return self._run_metrics_from_row(row)

    def list_run_metrics(self, limit: int = 10) -> list[RunMetrics]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM run_metrics
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._run_metrics_from_row(row) for row in rows]

    def _run_metrics_from_row(self, row: sqlite3.Row) -> RunMetrics:
        return RunMetrics(
            run_id=row["run_id"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=(
                datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
            ),
            pages_visited=row["pages_visited"],
            jobs_found=row["jobs_found"],
            valid_jobs=row["valid_jobs"],
            duplicate_jobs=row["duplicate_jobs"],
            failed_pages=row["failed_pages"],
            avg_steps_per_job=row["avg_steps_per_job"],
            estimated_token_cost=row["estimated_token_cost"] if row["cost_known"] else None,
        )

    @staticmethod
    def _save_jobs_with_connection(
        conn: sqlite3.Connection,
        jobs: list[JobPosting],
    ) -> None:
        provenance_by_url = {
            job.url: {
                "url_verification": str(getattr(job, "url_verification", "") or "unverified"),
                "tier": str(getattr(job, "tier", "") or ""),
                "category": str(getattr(job, "category", "") or ""),
                "internship_evidence_source": str(
                    getattr(job, "internship_evidence_source", "") or ""
                ),
                "internship_evidence_value": str(
                    getattr(job, "internship_evidence_value", "") or ""
                ),
            }
            for job in jobs
        }
        conn.executemany(
            """
            INSERT OR REPLACE INTO jobs (
                url, title, company, location, source, requirements,
                responsibilities, skills_json, posted_at, confidence,
                link_verification_json, provenance_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    job.url,
                    job.title,
                    job.company,
                    job.location,
                    job.source,
                    job.requirements,
                    job.responsibilities,
                    json.dumps(job.skills, ensure_ascii=False),
                    job.posted_at,
                    job.confidence,
                    json.dumps(
                        JobRepository._receipt_object(job), ensure_ascii=False
                    ),
                    json.dumps(provenance_by_url[job.url], ensure_ascii=False),
                )
                for job in jobs
            ],
        )

    def _migrate_jobs(self, conn: sqlite3.Connection) -> None:
        """Keep legacy rows and add link evidence defaults in place."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        if "link_verification_json" not in columns:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN link_verification_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "provenance_json" not in columns:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'"
            )

    @staticmethod
    def _receipt_object(job: JobPosting) -> dict[str, Any]:
        value = getattr(job, "link_verification", {})
        if value is None:
            return {}
        if isinstance(value, Mapping):
            return dict(value)
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump(mode="json")
            if isinstance(dumped, Mapping):
                return dict(dumped)
        raise TypeError("link_verification must be a mapping")

    @staticmethod
    def _json_object(value: str | None) -> dict[str, Any]:
        if not value:
            return {}
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}

    def get_cached_page(self, url: str, *, max_age_hours: float = 24.0) -> BrowserPage | None:
        """命中且未过 TTL 的缓存页；过期或不存在返回 None（调用方重抓）。

        canonical_url（内容实际来源的可浏览详情页，见 job_sources 官方 API
        分支）经 metadata 透传，缓存 TTL 内的重复运行保持链接可打开。
        """
        row = self._fetch_cache_row(url)
        if row is None:
            return None
        fetched_at = datetime.fromisoformat(row["fetched_at"])
        age_hours = (datetime.now(UTC) - fetched_at).total_seconds() / 3600
        if age_hours > max_age_hours:
            return None
        metadata: dict[str, Any] = json.loads(row["metadata_json"] or "{}")
        if row["canonical_url"]:
            metadata["canonical_url"] = row["canonical_url"]
        if row["content_origin"]:
            metadata["content_origin"] = row["content_origin"]
        observed = metadata.get("observed_at")
        metadata["cache_hit"] = True
        if observed:
            metadata["cache_age_seconds"] = max(
                0,
                (datetime.now(UTC) - datetime.fromisoformat(observed)).total_seconds(),
            )
        else:
            metadata["provenance_state"] = "legacy_unknown"
        return BrowserPage(
            url=row["url"],
            title=row["title"],
            content=row["content"],
            source=row["source"],
            metadata=metadata,
        )

    def cache_page(self, page: BrowserPage) -> None:
        public_keys = {"internship_evidence_source", "internship_evidence_value",
                       "candidate_id", "snapshot_id", "canonical_url", "observed_at", "content_origin",
                       "discovered_company", "discovered_location", "discovered_title", "tier",
                       "source_spec", "source_job_id", "evidence_level", "availability", "provenance_state"}
        metadata = {key: value for key, value in page.metadata.items()
                    if key in public_keys and isinstance(value, (str, int, float, bool))}
        metadata.setdefault("observed_at", datetime.now(UTC).isoformat())
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO page_cache
                    (url, content, title, source, fetched_at, canonical_url, content_origin, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    page.url,
                    page.content,
                    page.title,
                    page.source,
                    datetime.now(UTC).isoformat(),
                    str(page.metadata.get("canonical_url") or ""),
                    str(page.metadata.get("content_origin") or ""),
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )

    def _migrate_page_cache(self, conn: sqlite3.Connection) -> None:
        """旧库补 canonical_url 列（每次运行独立建库，正常走新建分支）。"""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(page_cache)")}
        if "canonical_url" not in columns:
            conn.execute("ALTER TABLE page_cache ADD COLUMN canonical_url TEXT NOT NULL DEFAULT ''")
        if "content_origin" not in columns:
            conn.execute(
                "ALTER TABLE page_cache ADD COLUMN content_origin TEXT NOT NULL DEFAULT ''"
            )
        if "metadata_json" not in columns:
            conn.execute("ALTER TABLE page_cache ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")

    def _fetch_cache_row(self, url: str) -> sqlite3.Row | None:
        with self._connection() as conn:
            return conn.execute(
                "SELECT url, content, title, source, fetched_at, canonical_url, content_origin, metadata_json"
                " FROM page_cache WHERE url = ?",
                (url,),
            ).fetchone()

    def _connect(self) -> sqlite3.Connection:
        conn = (sqlite3.connect(self._memory_uri, uri=True) if self._memory_uri
                else sqlite3.connect(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with self._operation_lock:
            conn = self._connect()
            try:
                with conn:
                    yield conn
            finally:
                conn.close()
