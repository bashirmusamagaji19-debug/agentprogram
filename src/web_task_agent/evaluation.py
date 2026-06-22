from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from web_task_agent.browser import BrowserClient, BrowserConfigurationError, FakeBrowserClient
from web_task_agent.demo_pages import DEMO_JOB_PAGES
from web_task_agent.extractor import PageExtractor
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import UserProfile
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.site_fixtures import PUBLIC_JOB_FIXTURE_PAGES
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow


class EvaluationTask(BaseModel):
    keyword: str
    location: str = "Remote"
    target_count: int = Field(default=2, ge=1)
    skills: list[str] = Field(default_factory=list)
    resume_text: str = ""
    seed_urls: list[str] = Field(default_factory=list)
    required_keywords: list[str] = Field(default_factory=lambda: ["AI", "LLM", "Agent"])


class TaskEvaluationResult(BaseModel):
    keyword: str
    location: str
    pages_visited: int
    valid_jobs: int
    success: bool
    failure_reason: str = ""
    failure_category: str = ""
    failure_details: str = ""


class EvaluationResult(BaseModel):
    total_tasks: int
    completed_tasks: int
    success_rate: float
    total_valid_jobs: int
    average_pages_visited: float
    failure_counts: dict[str, int] = Field(default_factory=dict)
    task_results: list[TaskEvaluationResult]
    report_path: Path | None = None


BrowserFactory = Callable[[EvaluationTask], BrowserClient]
ExtractorFactory = Callable[[EvaluationTask], PageExtractor]


class EvaluationRunner:
    def __init__(
        self,
        output_dir: str | Path = "evaluations",
        browser_factory: BrowserFactory | None = None,
        extractor_factory: ExtractorFactory | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.browser_factory = browser_factory or self._default_browser_factory
        self.extractor_factory = extractor_factory or self._default_extractor_factory

    def _default_browser_factory(self, task: EvaluationTask) -> BrowserClient:
        return FakeBrowserClient(DEMO_JOB_PAGES)

    def _default_extractor_factory(self, task: EvaluationTask) -> PageExtractor:
        return PageExtractor()

    async def run(self, tasks: list[EvaluationTask]) -> EvaluationResult:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        task_results: list[TaskEvaluationResult] = []

        for index, task in enumerate(tasks, start=1):
            run_dir = self.output_dir / f"task-{index:02d}"
            repo = JobRepository(run_dir / "agent.db")
            repo.initialize()
            workflow = WebTaskWorkflow(
                browser=self.browser_factory(task),
                extractor=self.extractor_factory(task),
                matcher=JobMatcher(),
                verifier=JobVerifier(required_keywords=task.required_keywords),
                repository=repo,
                reporter=MarkdownReporter(run_dir / "reports"),
            )
            try:
                state = await workflow.run(
                    UserProfile(
                        keyword=task.keyword,
                        location=task.location,
                        target_count=task.target_count,
                        skills=task.skills,
                        resume_text=task.resume_text,
                        seed_urls=task.seed_urls,
                    ),
                    run_id=f"eval-{index:02d}-{uuid4().hex[:6]}",
                )
            except BrowserConfigurationError as exc:
                task_results.append(
                    TaskEvaluationResult(
                        keyword=task.keyword,
                        location=task.location,
                        pages_visited=0,
                        valid_jobs=0,
                        success=False,
                        failure_reason="browser error",
                        failure_category="browser_error",
                        failure_details=str(exc),
                    )
                )
                continue

            metrics = state.metrics
            valid_jobs = metrics.valid_jobs if metrics else 0
            pages_visited = metrics.pages_visited if metrics else 0
            failure_category, failure_reason, failure_details = self._classify_failure(
                pages_visited=pages_visited,
                jobs_found=metrics.jobs_found if metrics else 0,
                valid_jobs=valid_jobs,
                failed_pages=metrics.failed_pages if metrics else 0,
            )
            failure_details = self._format_failed_url_errors(state) or failure_details
            task_results.append(
                TaskEvaluationResult(
                    keyword=task.keyword,
                    location=task.location,
                    pages_visited=pages_visited,
                    valid_jobs=valid_jobs,
                    success=valid_jobs > 0,
                    failure_reason=failure_reason,
                    failure_category=failure_category,
                    failure_details=failure_details,
                )
            )

        result = self._summarize(task_results)
        report_path = self.output_dir / "evaluation-report.md"
        report_path.write_text(self._render_report(result), encoding="utf-8")
        return result.model_copy(update={"report_path": report_path})

    def _summarize(self, task_results: list[TaskEvaluationResult]) -> EvaluationResult:
        total_tasks = len(task_results)
        completed_tasks = sum(1 for result in task_results if result.success)
        total_valid_jobs = sum(result.valid_jobs for result in task_results)
        total_pages = sum(result.pages_visited for result in task_results)
        failure_counts: dict[str, int] = {}
        for result in task_results:
            if result.failure_category:
                failure_counts[result.failure_category] = (
                    failure_counts.get(result.failure_category, 0) + 1
                )
        return EvaluationResult(
            total_tasks=total_tasks,
            completed_tasks=completed_tasks,
            success_rate=round(completed_tasks / total_tasks, 2) if total_tasks else 0.0,
            total_valid_jobs=total_valid_jobs,
            average_pages_visited=round(total_pages / total_tasks, 2) if total_tasks else 0.0,
            failure_counts=failure_counts,
            task_results=task_results,
        )

    def _classify_failure(
        self,
        *,
        pages_visited: int,
        jobs_found: int,
        valid_jobs: int,
        failed_pages: int,
    ) -> tuple[str, str, str]:
        if valid_jobs > 0:
            return "", "", ""
        if failed_pages > 0:
            return "browser_error", "browser error", f"failed_pages={failed_pages}"
        if pages_visited == 0:
            return "no_pages", "no pages returned", ""
        if jobs_found == 0:
            return "no_extracted_jobs", "no jobs extracted", ""
        return (
            "verification_filtered",
            "no valid jobs",
            f"jobs_found={jobs_found}; valid_jobs={valid_jobs}",
        )

    def _format_failed_url_errors(self, state) -> str:
        errors = state.metadata.get("failed_url_errors", [])
        if not errors:
            return ""
        return "; ".join(
            f"{item.get('url', '-')} -> {item.get('error', '-')}"
            for item in errors
        )

    def _render_report(self, result: EvaluationResult) -> str:
        lines = [
            "# Web Task Agent 评测报告",
            "",
            "## 汇总",
            "",
            f"- 任务总数: {result.total_tasks}",
            f"- 完成任务数: {result.completed_tasks}",
            f"- 任务成功率: {result.success_rate:.2f}",
            f"- 有效岗位总数: {result.total_valid_jobs}",
            f"- 平均访问页面数: {result.average_pages_visited:.2f}",
            "",
            "## 失败原因分布",
            "",
            "| 类别 | 数量 |",
            "|---|---:|",
        ]
        if result.failure_counts:
            for category, count in sorted(result.failure_counts.items()):
                lines.append(f"| {category} | {count} |")
        else:
            lines.append("| - | 0 |")

        lines.extend(
            [
                "",
                "## 任务明细",
                "",
                "| # | 关键词 | 地点 | 访问页面数 | 有效岗位数 | 状态 | 失败类别 | 失败原因 | 失败细节 |",
                "|---|---|---|---:|---:|---|---|---|---|",
            ]
        )
        for index, task_result in enumerate(result.task_results, start=1):
            status = "成功" if task_result.success else "失败"
            lines.append(
                "| "
                f"{index} | {task_result.keyword} | {task_result.location} | "
                f"{task_result.pages_visited} | {task_result.valid_jobs} | "
                f"{status} | {task_result.failure_category or '-'} | "
                f"{task_result.failure_reason or '-'} | "
                f"{task_result.failure_details or '-'} |"
            )
        lines.append("")
        return "\n".join(lines)


def build_default_tasks() -> list[EvaluationTask]:
    base_tasks = [
        ("AI intern", "Remote", ["Python", "LangGraph"]),
        ("LLM agent intern", "Remote", ["Python", "LLM"]),
        ("AI engineering intern", "Remote", ["Python", "FastAPI"]),
        ("machine learning platform intern", "Shanghai", ["Python", "SQL"]),
        ("agent workflow intern", "Remote", ["LangGraph", "Python"]),
        ("RAG intern", "Remote", ["Python", "RAG"]),
        ("browser agent intern", "Remote", ["Python", "browser-use"]),
        ("AI application intern", "Shanghai", ["Python", "FastAPI"]),
        ("LLM application intern", "Remote", ["LLM", "Python"]),
        ("AI backend intern", "Shanghai", ["FastAPI", "SQL"]),
    ]
    tasks: list[EvaluationTask] = []
    for keyword, location, skills in base_tasks:
        tasks.append(EvaluationTask(keyword=keyword, location=location, skills=skills))
        tasks.append(
            EvaluationTask(
                keyword=keyword,
                location=location,
                skills=skills[:1],
                resume_text="Built Python LangGraph agents and FastAPI services.",
            )
        )
    return tasks[:20]


def build_real_smoke_tasks() -> list[EvaluationTask]:
    return [
        EvaluationTask(
            keyword="AI intern",
            location="Remote",
            target_count=1,
            skills=["Python", "LLM"],
        ),
        EvaluationTask(
            keyword="LLM agent intern",
            location="Remote",
            target_count=1,
            skills=["Python", "LangGraph"],
        ),
        EvaluationTask(
            keyword="AI engineering intern",
            location="Remote",
            target_count=1,
            skills=["Python", "browser-use"],
        ),
    ]


def build_real_site_sample_tasks() -> list[EvaluationTask]:
    """Real, verified-accessible job posting URLs.

    Each URL individually verified with curl (HTTP 200, body > 10KB).
    URLs sourced from Greenhouse API (boards-api.greenhouse.io/v1/boards/).
    See docs/work-log/2026-06-22-real-site-verification.md for verification details.

    All 8 URLs return server-rendered HTML with full job descriptions.
    """
    return [
        # ── Anthropic (2) ──────────────────────────────────────────
        EvaluationTask(
            keyword="Applied AI Claude Evangelist",
            location="San Francisco, CA",
            target_count=1,
            skills=["AI", "demos", "customer"],
            seed_urls=["https://job-boards.greenhouse.io/anthropic/jobs/5116927008"],
        ),
        EvaluationTask(
            keyword="Technical Program Manager, API Platform",
            location="San Francisco, CA",
            target_count=1,
            skills=["API", "platform", "program management"],
            seed_urls=["https://job-boards.greenhouse.io/anthropic/jobs/5256303008"],
        ),
        # ── ScaleAI (3) ────────────────────────────────────────────
        EvaluationTask(
            keyword="AI Builder Intern",
            location="San Francisco, CA; New York, NY",
            target_count=1,
            skills=["AI", "Python", "intern"],
            seed_urls=["https://job-boards.greenhouse.io/scaleai/jobs/4703343005"],
        ),
        EvaluationTask(
            keyword="AI Deployment Strategist",
            location="San Francisco, CA; New York, NY",
            target_count=1,
            skills=["AI", "strategy", "deployment"],
            seed_urls=["https://job-boards.greenhouse.io/scaleai/jobs/4699458005"],
        ),
        EvaluationTask(
            keyword="AI Strategy Consultant",
            location="San Francisco, CA",
            target_count=1,
            skills=["AI", "consulting", "strategy"],
            seed_urls=["https://job-boards.greenhouse.io/scaleai/jobs/4472223005"],
        ),
        # ── Reddit (2) ─────────────────────────────────────────────
        EvaluationTask(
            keyword="Analytics Engineer",
            location="Remote - United States",
            target_count=1,
            skills=["SQL", "Python", "analytics"],
            seed_urls=["https://job-boards.greenhouse.io/reddit/jobs/7958354"],
        ),
        EvaluationTask(
            keyword="Analytics Engineer Toronto",
            location="Toronto, Canada",
            target_count=1,
            skills=["SQL", "Python", "analytics"],
            seed_urls=["https://job-boards.greenhouse.io/reddit/jobs/7958385"],
        ),
        # ── Discord (1) ────────────────────────────────────────────
        EvaluationTask(
            keyword="Director Developer Solutions",
            location="San Francisco Bay Area",
            target_count=1,
            skills=["developer relations", "platform", "leadership"],
            seed_urls=["https://job-boards.greenhouse.io/discord/jobs/8480100002"],
        ),
    ]


def build_public_job_fixture_tasks() -> list[EvaluationTask]:
    return [
        EvaluationTask(
            keyword="AI Agent Engineering Intern",
            location="Remote",
            target_count=1,
            skills=["Python", "LangGraph", "browser-use"],
        ),
        EvaluationTask(
            keyword="LLM Application Intern",
            location="Shanghai",
            target_count=1,
            skills=["Python", "FastAPI", "RAG"],
        ),
    ]


def build_public_job_fixture_browser(task: EvaluationTask) -> BrowserClient:
    return FakeBrowserClient(PUBLIC_JOB_FIXTURE_PAGES)
