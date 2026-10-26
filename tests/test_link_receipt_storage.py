import json
import sqlite3

from web_task_agent.models import JobPosting
from web_task_agent.storage import JobRepository


def make_job(**overrides):
    data = {
        "title": "Agent Intern",
        "company": "Example AI",
        "location": "Remote",
        "source": "fixture",
        "url": "https://example.com/jobs/link-receipt",
    }
    data.update(overrides)
    return JobPosting(**data)


def test_legacy_jobs_table_is_migrated_without_replacing_existing_rows(tmp_path):
    db_path = tmp_path / "agent.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE jobs (
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
            INSERT INTO jobs VALUES (
                'https://example.com/jobs/legacy', 'Legacy Intern', 'Legacy Co',
                'Shanghai', 'fixture', '', '', '[]', '', 0.5
            )
            """
        )

    repository = JobRepository(db_path)
    repository.initialize()

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        row = conn.execute(
            "SELECT title, link_verification_json, provenance_json FROM jobs"
        ).fetchone()
    jobs = repository.list_jobs()

    assert {"link_verification_json", "provenance_json"} <= columns
    assert row == ("Legacy Intern", "{}", "{}")
    assert jobs[0].title == "Legacy Intern"
    assert jobs[0].url_verification == "unverified"
    assert getattr(jobs[0], "link_verification", {}) == {}


def test_job_receipt_and_provenance_round_trip_as_json(tmp_path):
    db_path = tmp_path / "agent.db"
    repository = JobRepository(db_path)
    repository.initialize()
    receipt = {
        "status": "verified",
        "checked_at": "2026-10-26T08:30:00+00:00",
        "run_id": "run-link-1",
        "requested_url": "https://example.com/jobs/redirect",
        "final_url": "https://example.com/jobs/link-receipt",
        "method": "GET",
        "reason": "title and JD matched",
        "evidence_sha256": "a" * 64,
        "title_matched": True,
        "jd_matched": True,
        "active": True,
    }
    job = make_job().model_copy(
        update={
            "link_verification": receipt,
            "tier": "大厂",
            "category": "算法",
            "url_verification": "api-verified",
            "internship_evidence_source": "xiaomi:detail:recruit_type.id",
            "internship_evidence_value": "202",
        }
    )

    repository.save_jobs([job])
    loaded = repository.list_jobs()[0]

    assert loaded.link_verification == receipt
    assert loaded.tier == "大厂"
    assert loaded.category == "算法"
    assert loaded.url_verification == "api-verified"
    assert loaded.internship_evidence_source == "xiaomi:detail:recruit_type.id"
    assert loaded.internship_evidence_value == "202"
    with sqlite3.connect(db_path) as conn:
        link_json, provenance_json = conn.execute(
            "SELECT link_verification_json, provenance_json FROM jobs"
        ).fetchone()
    assert json.loads(link_json) == receipt
    assert json.loads(provenance_json) == {
        "url_verification": "api-verified",
        "tier": "大厂",
        "category": "算法",
        "internship_evidence_source": "xiaomi:detail:recruit_type.id",
        "internship_evidence_value": "202",
    }
