from web_task_agent.models import JobPosting
from web_task_agent.storage import JobRepository


def test_memory_repository_survives_separate_operations_and_is_isolated():
    first = JobRepository(":memory:")
    second = JobRepository(":memory:")
    first.initialize()
    second.initialize()
    job = JobPosting(title="AI Intern", company="Example", location="Remote",
                     source="fixture", url="https://example.com/jobs/1")
    receipt = first.save_jobs_once([job], idempotency_key="approval-1")
    repeated = first.save_jobs_once([job], idempotency_key="approval-1")
    assert receipt.saved_jobs == 1
    assert repeated.reused
    assert first.list_jobs() == [job]
    assert not second.list_jobs()


def test_memory_repository_serializes_concurrent_transactions():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    repository = JobRepository(":memory:")
    repository.initialize()
    barrier = Barrier(8)

    def save(index):
        barrier.wait(timeout=10)
        for item in range(8):
            repository.save_jobs_once([JobPosting(
                title=f"AI Intern {index}-{item}", company="Example", location="Remote", source="fixture",
                url=f"https://example.com/jobs/{index}-{item}",
            )], idempotency_key=f"{index}-{item}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(save, range(8)))
    assert len(repository.list_jobs()) == 64


def test_unknown_cost_round_trips_as_unknown_instead_of_zero():
    from web_task_agent.models import RunMetrics
    repository = JobRepository(":memory:")
    repository.initialize()
    unknown = RunMetrics(run_id="unknown-cost")
    assert unknown.estimated_token_cost is None
    repository.save_run_metrics(unknown)
    assert repository.get_run_metrics("unknown-cost").estimated_token_cost is None
    known_zero = RunMetrics(run_id="known-zero", estimated_token_cost=0.0)
    repository.save_run_metrics(known_zero)
    assert repository.get_run_metrics("known-zero").estimated_token_cost == 0.0
