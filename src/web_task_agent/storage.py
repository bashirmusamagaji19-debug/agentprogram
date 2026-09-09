from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from web_task_agent.models import BrowserPage, JobPosting, RunMetrics


@dataclass(frozen=True)
class SaveReceipt:
    idempotency_key: str
    saved_jobs: int
    reused: bool


class JobRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

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
                    confidence REAL NOT NULL
                )
                """
            )
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
                       responsibilities, skills_json, posted_at, confidence
                FROM jobs
                ORDER BY company, title
                """
            ).fetchall()

        return [
            JobPosting(
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
            )
            for row in rows
        ]

    def save_run_metrics(self, metrics: RunMetrics) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO run_metrics (
                    run_id, started_at, finished_at, pages_visited, jobs_found,
                    valid_jobs, duplicate_jobs, failed_pages, avg_steps_per_job,
                    estimated_token_cost
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    metrics.estimated_token_cost,
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
                datetime.fromisoformat(row["finished_at"])
                if row["finished_at"]
                else None
            ),
            pages_visited=row["pages_visited"],
            jobs_found=row["jobs_found"],
            valid_jobs=row["valid_jobs"],
            duplicate_jobs=row["duplicate_jobs"],
            failed_pages=row["failed_pages"],
            avg_steps_per_job=row["avg_steps_per_job"],
            estimated_token_cost=row["estimated_token_cost"],
        )

    @staticmethod
    def _save_jobs_with_connection(
        conn: sqlite3.Connection,
        jobs: list[JobPosting],
    ) -> None:
        conn.executemany(
            """
            INSERT OR REPLACE INTO jobs (
                url, title, company, location, source, requirements,
                responsibilities, skills_json, posted_at, confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                )
                for job in jobs
            ],
        )

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
        metadata: dict[str, Any] = {}
        if row["canonical_url"]:
            metadata["canonical_url"] = row["canonical_url"]
        if row["content_origin"]:
            metadata["content_origin"] = row["content_origin"]
        return BrowserPage(
            url=row["url"],
            title=row["title"],
            content=row["content"],
            source=row["source"],
            metadata=metadata,
        )

    def cache_page(self, page: BrowserPage) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO page_cache
                    (url, content, title, source, fetched_at, canonical_url, content_origin)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    page.url,
                    page.content,
                    page.title,
                    page.source,
                    datetime.now(UTC).isoformat(),
                    str(page.metadata.get("canonical_url") or ""),
                    str(page.metadata.get("content_origin") or ""),
                ),
            )

    def _migrate_page_cache(self, conn: sqlite3.Connection) -> None:
        """旧库补 canonical_url 列（每次运行独立建库，正常走新建分支）。"""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(page_cache)")}
        if "canonical_url" not in columns:
            conn.execute("ALTER TABLE page_cache ADD COLUMN canonical_url TEXT NOT NULL DEFAULT ''")
        if "content_origin" not in columns:
            conn.execute("ALTER TABLE page_cache ADD COLUMN content_origin TEXT NOT NULL DEFAULT ''")

    def _fetch_cache_row(self, url: str) -> sqlite3.Row | None:
        with self._connection() as conn:
            return conn.execute(
                "SELECT url, content, title, source, fetched_at, canonical_url, content_origin"
                " FROM page_cache WHERE url = ?",
                (url,),
            ).fetchone()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()
