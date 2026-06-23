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
        state.metadata["orchestration_mode"] = "sequential"
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
        state.metadata["orchestration_mode"] = "langgraph"
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
        if state.user.seed_urls:
            state.candidate_urls = state.user.seed_urls[: state.user.target_count]
            state.search_queries = []
            self._record_trace(
                state,
                "planner",
                f"selected {len(state.candidate_urls)} seed URLs",
            )
            return state
        state.search_queries = self._plan_queries(state.user)
        self._record_trace(
            state,
            "planner",
            f"planned {len(state.search_queries)} search queries",
        )
        return state

    async def _browser_node(self, state: WorkflowState) -> WorkflowState:
        pages_before = len(state.pages)
        failures_before = len(state.failed_urls)
        if state.candidate_urls:
            failed_url_errors = state.metadata.setdefault("failed_url_errors", [])
            for url in state.candidate_urls:
                try:
                    state.pages.append(await self.browser.open_url(url))
                except Exception as exc:
                    state.failed_urls.append(url)
                    failed_url_errors.append(
                        {
                            "url": url,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
            self._record_trace(
                state,
                "browser",
                (
                    f"opened {len(state.pages) - pages_before} pages; "
                    f"failed {len(state.failed_urls) - failures_before} URLs"
                ),
            )
            return state

        for query in state.search_queries:
            pages = await self.browser.search(query, target_count=state.user.target_count)
            state.pages.extend(pages)
            if len(state.pages) >= state.user.target_count:
                break
        self._record_trace(
            state,
            "browser",
            f"visited {len(state.pages) - pages_before} pages",
        )
        return state

    def _extractor_node(self, state: WorkflowState) -> WorkflowState:
        state.candidate_urls = [page.url for page in state.pages]
        state.metadata["extracted_jobs"] = [
            self.extractor.extract(page) for page in state.pages
        ]
        self._record_trace(
            state,
            "extractor",
            f"extracted {len(state.metadata['extracted_jobs'])} job candidates",
        )
        return state

    def _verifier_node(self, state: WorkflowState) -> WorkflowState:
        extracted_jobs = state.metadata.get("extracted_jobs", [])
        unique_jobs, duplicate_jobs = self.verifier.dedupe(extracted_jobs)
        verified_jobs: list = []
        filtered_jobs: list[dict[str, object]] = []
        for job in unique_jobs:
            result = self.verifier.verify(job)
            if result.is_valid:
                verified_jobs.append(job)
                continue
            filtered_jobs.append(
                {
                    "title": job.title,
                    "company": job.company,
                    "url": job.url,
                    "reasons": result.reasons,
                }
            )
        state.jobs = verified_jobs
        state.metadata["jobs_found"] = len(extracted_jobs)
        state.metadata["duplicate_jobs"] = len(duplicate_jobs)
        state.metadata["filtered_jobs"] = filtered_jobs
        self._record_trace(
            state,
            "verifier",
            (
                f"kept {len(state.jobs)} valid jobs; removed "
                f"{len(duplicate_jobs)} duplicates; filtered {len(filtered_jobs)} jobs"
            ),
        )
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
        self._record_trace(
            state,
            "matcher",
            f"scored {len(state.matches)} job matches",
        )
        return state

    def _reporter_node(self, state: WorkflowState) -> WorkflowState:
        if state.metrics is None:
            return state
        self.repository.save_jobs(state.jobs)
        self.repository.save_run_metrics(state.metrics)
        self._record_trace(
            state,
            "reporter",
            f"persisted {len(state.jobs)} jobs and wrote Markdown report",
        )
        report_path = self.reporter.write_report(
            user=state.user,
            jobs=state.jobs,
            matches=state.matches,
            metrics=state.metrics,
            execution_trace=state.metadata.get("execution_trace", []),
            orchestration_mode=state.metadata.get("orchestration_mode", "sequential"),
        )
        state.report_path = str(report_path)
        return state

    def _plan_queries(self, user: UserProfile) -> list[str]:
        return [
            f"{user.keyword} {user.location}",
            f"{user.keyword} LangGraph browser agent internship",
            f"{user.keyword} LLM agent internship",
        ]

    def _record_trace(self, state: WorkflowState, node: str, summary: str) -> None:
        trace = state.metadata.setdefault("execution_trace", [])
        trace.append({"node": node, "summary": summary})
