from __future__ import annotations

import argparse
import asyncio

from web_task_agent.browser import (
    BrowserConfigurationError,
    BrowserUseClient,
    FakeBrowserClient,
)
from web_task_agent.dashboard import HtmlDashboard
from web_task_agent.demo_pages import DEMO_JOB_PAGES
from web_task_agent.evaluation import (
    EvaluationRunner,
    build_public_job_fixture_browser,
    build_public_job_fixture_tasks,
    build_default_tasks,
    build_real_smoke_tasks,
)
from web_task_agent.extractor import PageExtractor
from web_task_agent.graph_export import LangGraphExporter
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import UserProfile
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Web Task Agent MVP.")
    parser.add_argument("--keyword")
    parser.add_argument("--location", default="Remote")
    parser.add_argument("--target-count", type=int, default=10)
    parser.add_argument("--skill", action="append", default=[])
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Use deterministic built-in demo pages.",
    )
    parser.add_argument("--db-path", default="agent.db")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Write a local HTML dashboard.",
    )
    parser.add_argument(
        "--langgraph",
        action="store_true",
        help="Run the main workflow through LangGraph nodes.",
    )
    parser.add_argument("--dashboard-dir", default="dashboards")
    parser.add_argument("--evaluate", action="store_true", help="Run the built-in evaluation task set.")
    parser.add_argument("--evaluation-count", type=int, default=20)
    parser.add_argument("--evaluation-dir", default="evaluations")
    parser.add_argument(
        "--real-smoke",
        action="store_true",
        help="Use real browser-use smoke tasks when --evaluate is enabled.",
    )
    parser.add_argument(
        "--fixture-sites",
        action="store_true",
        help="Use public job-board style fixture pages when --evaluate is enabled.",
    )
    parser.add_argument(
        "--export-graph",
        action="store_true",
        help="Write the LangGraph workflow as a Mermaid Markdown document.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


def build_browser(*, demo: bool) -> FakeBrowserClient | BrowserUseClient:
    return FakeBrowserClient(DEMO_JOB_PAGES) if demo else BrowserUseClient()


async def _run(args: argparse.Namespace) -> int:
    if args.export_graph:
        workflow = build_workflow(
            browser=FakeBrowserClient(DEMO_JOB_PAGES),
            db_path=args.db_path,
            report_dir=args.report_dir,
        )
        graph_path = LangGraphExporter().write_markdown(workflow)
        print(f"Graph written to: {graph_path}")
        return 0

    if args.evaluate:
        if args.fixture_sites:
            tasks = build_public_job_fixture_tasks()[: args.evaluation_count]
            runner = EvaluationRunner(
                args.evaluation_dir,
                browser_factory=build_public_job_fixture_browser,
            )
        elif args.real_smoke:
            tasks = build_real_smoke_tasks()[: args.evaluation_count]
            runner = EvaluationRunner(
                args.evaluation_dir,
                browser_factory=lambda task: BrowserUseClient(),
            )
        else:
            tasks = build_default_tasks()[: args.evaluation_count]
            runner = EvaluationRunner(args.evaluation_dir)
        result = await runner.run(tasks=tasks)
        print(f"Evaluation report written to: {result.report_path}")
        print(f"Task success rate: {result.success_rate:.2f}")
        print(f"Completed tasks: {result.completed_tasks}/{result.total_tasks}")
        if args.dashboard:
            dashboard_dir = HtmlDashboard(args.dashboard_dir).output_dir
            dashboard_dir.mkdir(parents=True, exist_ok=True)
            dashboard_path = dashboard_dir / "evaluation-summary.html"
            dashboard_path.write_text(
                HtmlDashboard(args.dashboard_dir).render_evaluation_summary(result),
                encoding="utf-8",
            )
            print(f"Evaluation dashboard written to: {dashboard_path}")
        return 0

    if not args.keyword:
        print("--keyword is required unless --evaluate is used.")
        return 2

    browser = build_browser(demo=args.demo)
    workflow = build_workflow(
        browser=browser,
        db_path=args.db_path,
        report_dir=args.report_dir,
    )
    try:
        user = UserProfile(
            keyword=args.keyword,
            location=args.location,
            target_count=args.target_count,
            skills=args.skill,
        )
        if args.langgraph:
            state = await workflow.run_with_langgraph(user)
        else:
            state = await workflow.run(user)
    except BrowserConfigurationError as exc:
        print(f"Real browser-use mode is not configured: {exc}")
        print("Use --demo for the deterministic local demo path.")
        return 2
    valid_jobs = state.metrics.valid_jobs if state.metrics else 0
    if args.langgraph:
        print("LangGraph workflow: enabled")
    print(f"Report written to: {state.report_path}")
    print(f"Valid jobs: {valid_jobs}")
    if args.dashboard and state.metrics:
        dashboard_path = HtmlDashboard(args.dashboard_dir).write_dashboard(
            user=state.user,
            jobs=state.jobs,
            matches=state.matches,
            metrics=state.metrics,
        )
        print(f"Dashboard written to: {dashboard_path}")
    return 0


def build_workflow(
    *,
    browser,
    db_path: str,
    report_dir: str,
) -> WebTaskWorkflow:
    repo = JobRepository(db_path)
    repo.initialize()
    return WebTaskWorkflow(
        browser=browser,
        extractor=PageExtractor(),
        matcher=JobMatcher(),
        verifier=JobVerifier(required_keywords=["AI", "LLM", "Agent"]),
        repository=repo,
        reporter=MarkdownReporter(report_dir),
    )


if __name__ == "__main__":
    raise SystemExit(main())
