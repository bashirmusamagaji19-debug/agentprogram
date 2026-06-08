from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from web_task_agent.browser import BrowserClient
from web_task_agent.extractor import PageExtractor
from web_task_agent.models import RunMetrics, UserProfile, WorkflowState
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier


class WebTaskWorkflow:
    def __init__(
        self,
        *,
        browser: BrowserClient,
        extractor: PageExtractor,
        verifier: JobVerifier,
        repository: JobRepository,
        reporter: MarkdownReporter,
    ) -> None:
        self.browser = browser
        self.extractor = extractor
        self.verifier = verifier
        self.repository = repository
        self.reporter = reporter

    async def run(
        self,
        user: UserProfile,
        run_id: str | None = None,
    ) -> WorkflowState:
        state = WorkflowState(user=user)
        state.metrics = RunMetrics(run_id=run_id or f"run-{uuid4().hex[:8]}")
        state.search_queries = self._plan_queries(user)

        for query in state.search_queries:
            pages = await self.browser.search(query, target_count=user.target_count)
            state.pages.extend(pages)
            if len(state.pages) >= user.target_count:
                break

        extracted_jobs = [self.extractor.extract(page) for page in state.pages]
        unique_jobs, duplicate_jobs = self.verifier.dedupe(extracted_jobs)
        valid_jobs = [
            job for job in unique_jobs if self.verifier.verify(job).is_valid
        ]
        state.jobs = valid_jobs

        metrics = state.metrics
        metrics.pages_visited = len(state.pages)
        metrics.jobs_found = len(extracted_jobs)
        metrics.valid_jobs = len(valid_jobs)
        metrics.duplicate_jobs = len(duplicate_jobs)
        metrics.failed_pages = len(state.failed_urls)
        metrics.avg_steps_per_job = (
            round(metrics.pages_visited / len(valid_jobs), 2)
            if valid_jobs
            else 0.0
        )
        metrics.finished_at = datetime.now(timezone.utc)

        self.repository.save_jobs(valid_jobs)
        self.repository.save_run_metrics(metrics)
        report_path = self.reporter.write_report(
            user=user,
            jobs=valid_jobs,
            metrics=metrics,
        )
        state.report_path = str(report_path)
        return state

    def _plan_queries(self, user: UserProfile) -> list[str]:
        return [
            f"{user.keyword} {user.location}",
            f"{user.keyword} LangGraph browser agent internship",
            f"{user.keyword} LLM agent internship",
        ]
