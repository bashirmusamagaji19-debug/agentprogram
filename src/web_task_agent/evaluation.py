from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from web_task_agent.browser import FakeBrowserClient
from web_task_agent.demo_pages import DEMO_JOB_PAGES
from web_task_agent.extractor import PageExtractor
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import UserProfile
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow


class EvaluationTask(BaseModel):
    keyword: str
    location: str = "Remote"
    target_count: int = Field(default=2, ge=1)
    skills: list[str] = Field(default_factory=list)
    resume_text: str = ""
    required_keywords: list[str] = Field(default_factory=lambda: ["AI", "LLM", "Agent"])


class TaskEvaluationResult(BaseModel):
    keyword: str
    location: str
    pages_visited: int
    valid_jobs: int
    success: bool
    failure_reason: str = ""


class EvaluationResult(BaseModel):
    total_tasks: int
    completed_tasks: int
    success_rate: float
    total_valid_jobs: int
    average_pages_visited: float
    task_results: list[TaskEvaluationResult]
    report_path: Path | None = None


class EvaluationRunner:
    def __init__(self, output_dir: str | Path = "evaluations") -> None:
        self.output_dir = Path(output_dir)

    async def run(self, tasks: list[EvaluationTask]) -> EvaluationResult:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        task_results: list[TaskEvaluationResult] = []

        for index, task in enumerate(tasks, start=1):
            run_dir = self.output_dir / f"task-{index:02d}"
            repo = JobRepository(run_dir / "agent.db")
            repo.initialize()
            workflow = WebTaskWorkflow(
                browser=FakeBrowserClient(DEMO_JOB_PAGES),
                extractor=PageExtractor(),
                matcher=JobMatcher(),
                verifier=JobVerifier(required_keywords=task.required_keywords),
                repository=repo,
                reporter=MarkdownReporter(run_dir / "reports"),
            )
            state = await workflow.run(
                UserProfile(
                    keyword=task.keyword,
                    location=task.location,
                    target_count=task.target_count,
                    skills=task.skills,
                    resume_text=task.resume_text,
                ),
                run_id=f"eval-{index:02d}-{uuid4().hex[:6]}",
            )
            metrics = state.metrics
            valid_jobs = metrics.valid_jobs if metrics else 0
            pages_visited = metrics.pages_visited if metrics else 0
            task_results.append(
                TaskEvaluationResult(
                    keyword=task.keyword,
                    location=task.location,
                    pages_visited=pages_visited,
                    valid_jobs=valid_jobs,
                    success=valid_jobs > 0,
                    failure_reason="" if valid_jobs > 0 else "no valid jobs",
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
        return EvaluationResult(
            total_tasks=total_tasks,
            completed_tasks=completed_tasks,
            success_rate=round(completed_tasks / total_tasks, 2) if total_tasks else 0.0,
            total_valid_jobs=total_valid_jobs,
            average_pages_visited=round(total_pages / total_tasks, 2) if total_tasks else 0.0,
            task_results=task_results,
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
            "## 任务明细",
            "",
            "| # | 关键词 | 地点 | 访问页面数 | 有效岗位数 | 状态 | 失败原因 |",
            "|---|---|---|---:|---:|---|---|",
        ]
        for index, task_result in enumerate(result.task_results, start=1):
            status = "成功" if task_result.success else "失败"
            lines.append(
                "| "
                f"{index} | {task_result.keyword} | {task_result.location} | "
                f"{task_result.pages_visited} | {task_result.valid_jobs} | "
                f"{status} | {task_result.failure_reason or '-'} |"
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
