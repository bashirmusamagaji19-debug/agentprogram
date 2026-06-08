from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from web_task_agent.browser import BrowserClient
from web_task_agent.extractor import PageExtractor
from web_task_agent.matcher import JobMatcher
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
        matcher: JobMatcher,
        verifier: JobVerifier,
        repository: JobRepository,
        reporter: MarkdownReporter,
    ) -> None:
        self.browser = browser
        self.extractor = extractor
        self.matcher = matcher
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
        state = await self._plan_node(state)
        state = await self._browser_node(state)
        state = self._extractor_node(state)
        state = self._verifier_node(state)
        state = self._matcher_node(state)
        state = self._reporter_node(state)
        return state

    async def run_with_langgraph(
        self,
        user: UserProfile,
        run_id: str | None = None,
    ) -> WorkflowState:
        state = WorkflowState(user=user)
        state.metrics = RunMetrics(run_id=run_id or f"run-{uuid4().hex[:8]}")
        result = await self.build_langgraph().ainvoke(state)
        if isinstance(result, WorkflowState):
            return result
        return WorkflowState.model_validate(result)

    def build_langgraph(self):
        graph = StateGraph(WorkflowState)
        graph.add_node("planner", self._plan_node)
        graph.add_node("browser", self._browser_node)
        graph.add_node("extractor", self._extractor_node)
        graph.add_node("verifier", self._verifier_node)
        graph.add_node("matcher", self._matcher_node)
        graph.add_node("reporter", self._reporter_node)
        graph.add_edge(START, "planner")
        graph.add_edge("planner", "browser")
        graph.add_edge("browser", "extractor")
        graph.add_edge("extractor", "verifier")
        graph.add_edge("verifier", "matcher")
        graph.add_edge("matcher", "reporter")
        graph.add_edge("reporter", END)
        return graph.compile()

    async def _plan_node(self, state: WorkflowState) -> WorkflowState:
        state.search_queries = self._plan_queries(state.user)
        return state

    async def _browser_node(self, state: WorkflowState) -> WorkflowState:
        for query in state.search_queries:
            pages = await self.browser.search(query, target_count=state.user.target_count)
            state.pages.extend(pages)
            if len(state.pages) >= state.user.target_count:
                break
        return state

    def _extractor_node(self, state: WorkflowState) -> WorkflowState:
        state.candidate_urls = [page.url for page in state.pages]
        state.metadata["extracted_jobs"] = [
            self.extractor.extract(page) for page in state.pages
        ]
        return state

    def _verifier_node(self, state: WorkflowState) -> WorkflowState:
        extracted_jobs = state.metadata.get("extracted_jobs", [])
        unique_jobs, duplicate_jobs = self.verifier.dedupe(extracted_jobs)
        state.jobs = [
            job for job in unique_jobs if self.verifier.verify(job).is_valid
        ]
        state.metadata["jobs_found"] = len(extracted_jobs)
        state.metadata["duplicate_jobs"] = len(duplicate_jobs)
        return state

    def _matcher_node(self, state: WorkflowState) -> WorkflowState:
        state.matches = self.matcher.match_many(user=state.user, jobs=state.jobs)
        metrics = state.metrics
        if metrics:
            metrics.pages_visited = len(state.pages)
            metrics.jobs_found = int(state.metadata.get("jobs_found", 0))
            metrics.valid_jobs = len(state.jobs)
            metrics.duplicate_jobs = int(state.metadata.get("duplicate_jobs", 0))
            metrics.failed_pages = len(state.failed_urls)
            metrics.avg_steps_per_job = (
                round(metrics.pages_visited / len(state.jobs), 2)
                if state.jobs
                else 0.0
            )
            metrics.finished_at = datetime.now(timezone.utc)
        return state

    def _reporter_node(self, state: WorkflowState) -> WorkflowState:
        if state.metrics is None:
            return state
        self.repository.save_jobs(state.jobs)
        self.repository.save_run_metrics(state.metrics)
        report_path = self.reporter.write_report(
            user=state.user,
            jobs=state.jobs,
            matches=state.matches,
            metrics=state.metrics,
        )
        state.report_path = str(report_path)
        return state

    def _plan_queries(self, user: UserProfile) -> list[str]:
        return [
            f"{user.keyword} {user.location}",
            f"{user.keyword} LangGraph browser agent internship",
            f"{user.keyword} LLM agent internship",
        ]
