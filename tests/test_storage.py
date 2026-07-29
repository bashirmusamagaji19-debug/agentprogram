from datetime import UTC, datetime

from web_task_agent.models import JobPosting, RunMetrics
from web_task_agent.storage import JobRepository


def make_job(**overrides):
    data = {
        "title": "AI Engineering Intern",
        "company": "Example AI",
        "location": "Remote",
        "source": "fixture",
        "url": "https://example.com/jobs/1",
        "requirements": "Python, LLM",
        "responsibilities": "Build agents",
        "skills": ["Python", "LLM"],
        "confidence": 0.9,
    }
    data.update(overrides)
    return JobPosting(**data)


def test_repository_saves_and_lists_jobs(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()
    job = make_job()

    repo.save_jobs([job])
    jobs = repo.list_jobs()

    assert len(jobs) == 1
    assert jobs[0].title == "AI Engineering Intern"
    assert jobs[0].skills == ["Python", "LLM"]


def test_repository_replaces_job_by_url(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()
    repo.save_jobs([make_job(title="Old Title")])

    repo.save_jobs([make_job(title="New Title")])
    jobs = repo.list_jobs()

    assert len(jobs) == 1
    assert jobs[0].title == "New Title"


def test_save_jobs_once_records_receipt_and_reuses_duplicate_key(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()

    first = repo.save_jobs_once(
        [make_job(title="AI Agent Intern")],
        idempotency_key="approval-1",
    )
    second = repo.save_jobs_once(
        [make_job(title="Changed title")],
        idempotency_key="approval-1",
    )

    assert first.reused is False
    assert first.saved_jobs == 1
    assert second.reused is True
    assert second.saved_jobs == 1
    assert repo.list_jobs()[0].title == "AI Agent Intern"


def test_save_jobs_once_rejects_blank_idempotency_key(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()

    try:
        repo.save_jobs_once([make_job()], idempotency_key=" ")
    except ValueError as exc:
        assert str(exc) == "idempotency_key must not be blank"
    else:
        raise AssertionError("blank idempotency key was accepted")


def test_repository_saves_run_metrics(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()
    metrics = RunMetrics(run_id="run-1", pages_visited=2, jobs_found=2, valid_jobs=1)

    repo.save_run_metrics(metrics)
    loaded = repo.get_run_metrics("run-1")

    assert loaded is not None
    assert loaded.pages_visited == 2
    assert loaded.valid_jobs == 1
    assert loaded.started_at.tzinfo == UTC


def test_repository_round_trips_finished_at_as_utc(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()
    metrics = RunMetrics(
        run_id="run-1",
        started_at=datetime(2026, 6, 8, 1, 0, tzinfo=UTC),
        finished_at=datetime(2026, 6, 8, 1, 1, tzinfo=UTC),
    )

    repo.save_run_metrics(metrics)
    loaded = repo.get_run_metrics("run-1")

    assert loaded is not None
    assert loaded.started_at == metrics.started_at
    assert loaded.finished_at == metrics.finished_at


def test_repository_returns_none_for_missing_run_metrics(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()

    assert repo.get_run_metrics("missing") is None


def test_repository_lists_recent_run_metrics(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()
    older = RunMetrics(
        run_id="run-old",
        started_at=datetime(2026, 6, 8, 1, 0, tzinfo=UTC),
        finished_at=datetime(2026, 6, 8, 1, 1, tzinfo=UTC),
        valid_jobs=1,
    )
    newer = RunMetrics(
        run_id="run-new",
        started_at=datetime(2026, 6, 8, 2, 0, tzinfo=UTC),
        finished_at=datetime(2026, 6, 8, 2, 1, tzinfo=UTC),
        valid_jobs=2,
    )

    repo.save_run_metrics(older)
    repo.save_run_metrics(newer)

    runs = repo.list_run_metrics(limit=1)
    assert [run.run_id for run in runs] == ["run-new"]
    assert runs[0].valid_jobs == 2
