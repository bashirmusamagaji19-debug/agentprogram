from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from web_task_agent.models import JobPosting, RunMetrics


class JobRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
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

    def save_jobs(self, jobs: list[JobPosting]) -> None:
        with self._connect() as conn:
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

    def list_jobs(self) -> list[JobPosting]:
        with self._connect() as conn:
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
        with self._connect() as conn:
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
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM run_metrics WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None

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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
