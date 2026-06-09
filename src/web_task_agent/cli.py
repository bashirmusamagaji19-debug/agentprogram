from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from web_task_agent import __version__
from web_task_agent.action_plan import ActionPlanWriter
from web_task_agent.browser import (
    BrowserConfigurationError,
    BrowserUseClient,
    FakeBrowserClient,
)
from web_task_agent.dashboard import HtmlDashboard
from web_task_agent.demo_pages import DEMO_JOB_PAGES
from web_task_agent.evaluation import (
    EvaluationTask,
    EvaluationRunner,
    build_public_job_fixture_browser,
    build_public_job_fixture_tasks,
    build_default_tasks,
    build_real_smoke_tasks,
)
from web_task_agent.extractor import PageExtractor
from web_task_agent.graph_export import LangGraphExporter
from web_task_agent.llm_extractor import DemoLlmFieldExtractor
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import MatchResult, UserProfile
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.skill_gap import summarize_skill_gaps
from web_task_agent.site_fixtures import PUBLIC_JOB_FIXTURE_PAGES
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Web Task Agent MVP.")
    parser.add_argument(
        "--version",
        action="version",
        version=f"web-task-agent {__version__}",
    )
    parser.add_argument("--keyword")
    parser.add_argument("--location", default="Remote")
    parser.add_argument("--target-count", type=int, default=10)
    parser.add_argument("--skill", action="append", default=[])
    parser.add_argument(
        "--seed-url",
        action="append",
        default=[],
        help="Open an exact job URL instead of searching. Can be repeated.",
    )
    parser.add_argument(
        "--resume-text",
        action="append",
        default=[],
        help="Inline resume text to use as matching signal. Can be repeated.",
    )
    parser.add_argument(
        "--resume-file",
        action="append",
        default=[],
        help="UTF-8 resume Markdown/text file to use as matching signal. Can be repeated.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Use deterministic built-in demo pages.",
    )
    parser.add_argument("--db-path", default="agent.db")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument(
        "--json-output",
        help="Write the completed workflow state to a machine-readable JSON file.",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Write a local HTML dashboard.",
    )
    parser.add_argument(
        "--action-plan",
        action="store_true",
        help="Write a Markdown action plan from matched jobs and skill gaps.",
    )
    parser.add_argument(
        "--langgraph",
        action="store_true",
        help="Run the main workflow through LangGraph nodes.",
    )
    parser.add_argument(
        "--llm-extractor-demo",
        action="store_true",
        help="Use a deterministic LLM-style structured extractor demo.",
    )
    parser.add_argument("--dashboard-dir", default="dashboards")
    parser.add_argument("--action-plan-dir", default="action-plans")
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
    parser.add_argument(
        "--history",
        action="store_true",
        help="Print recent workflow runs from SQLite and exit.",
    )
    parser.add_argument("--history-limit", type=int, default=10)
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Run local environment checks and exit.",
    )
    parser.add_argument(
        "--list-fixture-urls",
        action="store_true",
        help="Print built-in public job fixture URLs and exit.",
    )
    parser.add_argument(
        "--print-demo-script",
        action="store_true",
        help="Print a copyable local interview demo command script and exit.",
    )
    parser.add_argument(
        "--compare-llm-extractor",
        action="store_true",
        help="Compare rule extraction with the deterministic LLM extractor demo.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


def build_browser(*, demo: bool) -> FakeBrowserClient | BrowserUseClient:
    return FakeBrowserClient(DEMO_JOB_PAGES) if demo else BrowserUseClient()


async def _run(args: argparse.Namespace) -> int:
    if args.doctor:
        print_doctor_report(
            report_dir=args.report_dir,
            dashboard_dir=args.dashboard_dir,
            action_plan_dir=args.action_plan_dir,
            db_path=args.db_path,
        )
        return 0

    if args.list_fixture_urls:
        print_fixture_urls()
        return 0

    if args.print_demo_script:
        print_demo_script()
        return 0

    if args.compare_llm_extractor:
        result = await run_llm_extractor_comparison(args)
        print("LLM extractor comparison")
        print(
            "baseline: "
            f"{result['baseline']['completed_tasks']}/{result['baseline']['total_tasks']}"
        )
        print(
            "llm-demo: "
            f"{result['llm_demo']['completed_tasks']}/{result['llm_demo']['total_tasks']}"
        )
        if args.json_output:
            json_path = write_mapping_json_output(result, args.json_output)
            print(f"Comparison JSON written to: {json_path}")
        return 0

    if args.history:
        repo = JobRepository(args.db_path)
        repo.initialize()
        print_run_history(repo.list_run_metrics(limit=args.history_limit))
        return 0

    if args.export_graph:
        workflow = build_workflow(
            browser=FakeBrowserClient(DEMO_JOB_PAGES),
            db_path=args.db_path,
            report_dir=args.report_dir,
            llm_extractor_demo=args.llm_extractor_demo,
        )
        graph_path = LangGraphExporter().write_markdown(workflow)
        print(f"Graph written to: {graph_path}")
        return 0

    if args.evaluate:
        try:
            resume_text = load_resume_text(args.resume_text, args.resume_file)
        except FileNotFoundError as exc:
            print(f"Resume file not found: {exc.filename}")
            return 2
        if args.seed_url:
            tasks = [
                EvaluationTask(
                    keyword=args.keyword or "seed URLs",
                    location=args.location,
                    target_count=min(args.target_count, len(args.seed_url)),
                    skills=args.skill,
                    resume_text=resume_text,
                    seed_urls=args.seed_url,
                )
            ]
            if args.fixture_sites:
                runner = EvaluationRunner(
                    args.evaluation_dir,
                    browser_factory=build_public_job_fixture_browser,
                    extractor_factory=build_extractor_factory(args),
                )
            elif args.real_smoke:
                runner = EvaluationRunner(
                    args.evaluation_dir,
                    browser_factory=lambda task: BrowserUseClient(),
                    extractor_factory=build_extractor_factory(args),
                )
            else:
                runner = EvaluationRunner(
                    args.evaluation_dir,
                    extractor_factory=build_extractor_factory(args),
                )
        elif args.fixture_sites:
            tasks = build_public_job_fixture_tasks()[: args.evaluation_count]
            runner = EvaluationRunner(
                args.evaluation_dir,
                browser_factory=build_public_job_fixture_browser,
                extractor_factory=build_extractor_factory(args),
            )
        elif args.real_smoke:
            tasks = build_real_smoke_tasks()[: args.evaluation_count]
            runner = EvaluationRunner(
                args.evaluation_dir,
                browser_factory=lambda task: BrowserUseClient(),
                extractor_factory=build_extractor_factory(args),
            )
        else:
            tasks = build_default_tasks()[: args.evaluation_count]
            runner = EvaluationRunner(
                args.evaluation_dir,
                extractor_factory=build_extractor_factory(args),
            )
        result = await runner.run(tasks=tasks)
        if args.llm_extractor_demo:
            print("LLM extractor demo: enabled")
        print(f"Evaluation report written to: {result.report_path}")
        print(f"Task success rate: {result.success_rate:.2f}")
        print(f"Completed tasks: {result.completed_tasks}/{result.total_tasks}")
        if args.json_output:
            json_path = write_model_json_output(result, args.json_output)
            print(f"Evaluation JSON written to: {json_path}")
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

    if not args.keyword and not args.seed_url:
        print("--keyword is required unless --evaluate is used.")
        return 2

    browser = build_browser(demo=args.demo)
    workflow = build_workflow(
        browser=browser,
        db_path=args.db_path,
        report_dir=args.report_dir,
        llm_extractor_demo=args.llm_extractor_demo,
    )
    try:
        resume_text = load_resume_text(args.resume_text, args.resume_file)
    except FileNotFoundError as exc:
        print(f"Resume file not found: {exc.filename}")
        return 2
    try:
        user = UserProfile(
            keyword=args.keyword or "seed URLs",
            location=args.location,
            target_count=args.target_count,
            skills=args.skill,
            resume_text=resume_text,
            seed_urls=args.seed_url,
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
    if args.llm_extractor_demo:
        print("LLM extractor demo: enabled")
        state.metadata["extractor_mode"] = "llm-demo"
    print(f"Report written to: {state.report_path}")
    print(f"Valid jobs: {valid_jobs}")
    artifact_links = {}
    if args.action_plan and state.metrics:
        plan_path = ActionPlanWriter(args.action_plan_dir).write_plan(
            run_id=state.metrics.run_id,
            user=state.user,
            jobs=state.jobs,
            matches=state.matches,
        )
        state.metadata["action_plan_path"] = plan_path.as_posix()
        state.metadata["top_action_gaps"] = top_action_gap_items(state.matches)
        artifact_links["行动计划"] = plan_path
        state.report_path = str(
            MarkdownReporter(args.report_dir).write_report(
                user=state.user,
                jobs=state.jobs,
                matches=state.matches,
                metrics=state.metrics,
                artifact_links=artifact_links,
                execution_trace=state.metadata.get("execution_trace", []),
            )
        )
        print(f"Action plan written to: {plan_path}")
        print(f"Top action gaps: {format_top_action_gaps(state.matches)}")
    if args.dashboard and state.metrics:
        dashboard_path = HtmlDashboard(args.dashboard_dir).write_dashboard(
            user=state.user,
            jobs=state.jobs,
            matches=state.matches,
            metrics=state.metrics,
            search_queries=state.search_queries,
            failed_url_errors=state.metadata.get("failed_url_errors", []),
            artifact_links=artifact_links,
            execution_trace=state.metadata.get("execution_trace", []),
        )
        state.metadata["dashboard_path"] = dashboard_path.as_posix()
        artifact_links["Dashboard"] = dashboard_path
        state.report_path = str(
            MarkdownReporter(args.report_dir).write_report(
                user=state.user,
                jobs=state.jobs,
                matches=state.matches,
                metrics=state.metrics,
                artifact_links=artifact_links,
                execution_trace=state.metadata.get("execution_trace", []),
            )
        )
        print(f"Dashboard written to: {dashboard_path}")
    if args.json_output:
        json_path = write_json_output(state, args.json_output)
        print(f"JSON output written to: {json_path}")
    return 0


def load_resume_text(inline_texts: list[str], file_paths: list[str]) -> str:
    chunks = [text.strip() for text in inline_texts if text.strip()]
    for file_path in file_paths:
        path = Path(file_path)
        chunks.append(path.read_text(encoding="utf-8").strip())
    return "\n\n".join(chunk for chunk in chunks if chunk)


def write_json_output(state, output_path: str) -> Path:
    return write_model_json_output(state, output_path)


def format_top_action_gaps(matches: list[MatchResult]) -> str:
    gaps = top_action_gap_items(matches)
    if not gaps:
        return "none"
    return ", ".join(f"{gap['skill']} ({gap['count']})" for gap in gaps)


def top_action_gap_items(matches: list[MatchResult]) -> list[dict[str, int | str]]:
    return [
        {"skill": skill, "count": count}
        for skill, count in summarize_skill_gaps(matches)[:3]
    ]


def write_model_json_output(model, output_path: str) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def write_mapping_json_output(payload: dict, output_path: str) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


async def run_llm_extractor_comparison(args: argparse.Namespace) -> dict:
    task = EvaluationTask(
        keyword="AI intern",
        target_count=1,
        seed_urls=["https://example.com/jobs/unstructured-ai-agent-intern"],
    )
    baseline = await EvaluationRunner(args.evaluation_dir).run(tasks=[task])
    llm_demo = await EvaluationRunner(
        args.evaluation_dir,
        extractor_factory=lambda task: PageExtractor(
            llm_field_extractor=DemoLlmFieldExtractor(),
        ),
    ).run(tasks=[task])
    return {
        "seed_url": task.seed_urls[0],
        "baseline": baseline.model_dump(mode="json"),
        "llm_demo": llm_demo.model_dump(mode="json"),
    }


def print_run_history(runs) -> None:
    print("Recent runs")
    if not runs:
        print("No runs found.")
        return
    for run in runs:
        finished = run.finished_at.isoformat() if run.finished_at else "-"
        print(
            f"{run.run_id} | started={run.started_at.isoformat()} | "
            f"finished={finished} | valid_jobs={run.valid_jobs} | "
            f"pages={run.pages_visited} | failed_pages={run.failed_pages}"
        )


def print_doctor_report(
    *,
    report_dir: str,
    dashboard_dir: str,
    action_plan_dir: str,
    db_path: str,
) -> None:
    print("Environment doctor")
    print(f"python: {sys.executable}")
    print(f"virtualenv: {virtualenv_status()}")
    for module_name in ["langgraph", "browser_use", "pydantic"]:
        status = "ok" if importlib.util.find_spec(module_name) else "missing"
        print(f"{module_name}: {status}")
    print(f"database_parent: {writable_status(Path(db_path).parent)}")
    print(f"reports: {writable_status(Path(report_dir))}")
    print(f"dashboards: {writable_status(Path(dashboard_dir))}")
    print(f"action_plans: {writable_status(Path(action_plan_dir))}")


def virtualenv_status() -> str:
    return "active" if sys.prefix != getattr(sys, "base_prefix", sys.prefix) else "inactive"


def print_fixture_urls() -> None:
    print("Fixture job URLs")
    for page in PUBLIC_JOB_FIXTURE_PAGES:
        print(f"{page.title} | {page.url}")


def print_demo_script() -> None:
    print("Demo script")
    commands = [
        r".\.venv\Scripts\web-task-agent.exe --doctor",
        r".\.venv\Scripts\web-task-agent.exe --list-fixture-urls",
        (
            r'.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" '
            r"--target-count 2 --skill Python --skill LangGraph --demo "
            r"--dashboard --action-plan --json-output outputs\result.json"
        ),
        (
            r'.\.venv\Scripts\web-task-agent.exe --seed-url '
            r'"https://example.com/jobs/ai-engineering-intern" --demo '
            r"--target-count 1 --json-output outputs\seed-demo.json --dashboard"
        ),
        (
            r'.\.venv\Scripts\web-task-agent.exe --seed-url '
            r'"https://example.com/jobs/unstructured-ai-agent-intern" --demo '
            r"--target-count 1 --llm-extractor-demo "
            r"--json-output outputs\unstructured-llm-demo.json --dashboard"
        ),
        r".\.venv\Scripts\web-task-agent.exe --history",
        (
            r".\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites "
            r"--json-output evaluations\fixture-result.json"
        ),
    ]
    for index, command in enumerate(commands, start=1):
        print(f"{index}. {command}")


def writable_status(path: Path) -> str:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return f"not writable ({exc})"
    return "writable"


def build_workflow(
    *,
    browser,
    db_path: str,
    report_dir: str,
    llm_extractor_demo: bool = False,
) -> WebTaskWorkflow:
    repo = JobRepository(db_path)
    repo.initialize()
    return WebTaskWorkflow(
        browser=browser,
        extractor=PageExtractor(
            llm_field_extractor=DemoLlmFieldExtractor() if llm_extractor_demo else None,
        ),
        matcher=JobMatcher(),
        verifier=JobVerifier(required_keywords=["AI", "LLM", "Agent"]),
        repository=repo,
        reporter=MarkdownReporter(report_dir),
    )


def build_extractor_factory(args: argparse.Namespace):
    if not args.llm_extractor_demo:
        return None
    return lambda task: PageExtractor(llm_field_extractor=DemoLlmFieldExtractor())


if __name__ == "__main__":
    raise SystemExit(main())
