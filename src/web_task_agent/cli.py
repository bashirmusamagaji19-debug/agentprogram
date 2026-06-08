from __future__ import annotations

import argparse
import asyncio

from web_task_agent.browser import BrowserUseClient, FakeBrowserClient
from web_task_agent.demo_pages import DEMO_JOB_PAGES
from web_task_agent.extractor import PageExtractor
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import UserProfile
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Web Task Agent MVP.")
    parser.add_argument("--keyword", required=True)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> int:
    if not args.demo:
        print("Real browser-use mode is not implemented yet. Re-run with --demo.")
        return 2

    repo = JobRepository(args.db_path)
    repo.initialize()
    browser = FakeBrowserClient(DEMO_JOB_PAGES) if args.demo else BrowserUseClient()
    workflow = WebTaskWorkflow(
        browser=browser,
        extractor=PageExtractor(),
        matcher=JobMatcher(),
        verifier=JobVerifier(required_keywords=["AI", "LLM", "Agent"]),
        repository=repo,
        reporter=MarkdownReporter(args.report_dir),
    )
    state = await workflow.run(
        UserProfile(
            keyword=args.keyword,
            location=args.location,
            target_count=args.target_count,
            skills=args.skill,
        )
    )
    valid_jobs = state.metrics.valid_jobs if state.metrics else 0
    print(f"Report written to: {state.report_path}")
    print(f"Valid jobs: {valid_jobs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
