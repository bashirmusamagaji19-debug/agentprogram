"""Run the shared application service and retain its actual acceptance evidence."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from web_task_agent.action_plan import ActionPlanWriter
from web_task_agent.agent_approval import ApprovalDecision
from web_task_agent.agent_components import AgentServices, build_browser_components
from web_task_agent.agent_run_context import read_run_context, require_run_identity
from web_task_agent.agent_service import AgentRunOptions, AgentRunRequest, resume_agent_run, start_agent_run
from web_task_agent.browser import FakeBrowserClient
from web_task_agent.dashboard import HtmlDashboard
from web_task_agent.extractor import PageExtractor
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import BrowserPage, UserProfile
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["demo", "official", "aggregator", "seed_urls"])
    parser.add_argument("--keyword")
    parser.add_argument("--location")
    parser.add_argument("--target-count", type=int)
    parser.add_argument("--planner-strategy", choices=["policy", "always", "adaptive"])
    parser.add_argument("--provider", choices=["deepseek", "qwen"])
    parser.add_argument("--hitl", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--resume", choices=["approve", "reject"])
    parser.add_argument("--approval-id")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--deadline-seconds", type=float)
    parser.add_argument("--specs", help="Comma-separated official source specs")
    parser.add_argument("--seed-url", action="append", default=[])
    parser.add_argument("--aggregator-path")
    parser.add_argument("--aggregator-url")
    return parser


def validate_arguments(parser, args):
    if args.resume:
        if not args.approval_id or not args.run_id:
            parser.error("resume requires --approval-id and --run-id")
        if any((args.mode, args.keyword, args.location, args.target_count, args.planner_strategy,
                args.provider, args.hitl, args.specs, args.seed_url, args.aggregator_path,
                args.aggregator_url, args.deadline_seconds)):
            parser.error("resume uses stored configuration; start/source flags are mutually exclusive")
    elif args.approval_id:
        parser.error("--approval-id requires --resume")
    if args.run_id:
        require_run_identity(args.run_id)


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_acceptance(args):
    run_dir = args.output_dir.resolve()
    previous = {}
    if args.resume:
        context = read_run_context(run_dir, run_id=args.run_id, owner_id="acceptance")
        user, options, run_id = context.user, AgentRunOptions(**context.options), context.run_id
        browser = FakeBrowserClient([])
        if (run_dir / "result.json").is_file():
            previous = json.loads((run_dir / "result.json").read_text(encoding="utf-8")).get("metadata", {})
    else:
        request = AgentRunRequest(
            data_mode=args.mode or "demo", keyword=args.keyword or "AI intern", location=args.location or "Remote",
            target_count=args.target_count or 3, engine="hybrid", hitl=args.hitl,
            planner_strategy=args.planner_strategy or "policy", planner_provider=args.provider,
            deadline_seconds=args.deadline_seconds or 180, seed_urls=args.seed_url,
            aggregator_path=args.aggregator_path, aggregator_url=args.aggregator_url,
            official_specs=args.specs.split(",") if args.specs else None,
            skills=["Python", "LangGraph", "LLM"],
        )
        options = request.options()
        run_id = args.run_id or f"run-{uuid4().hex[:8]}"
        run_dir.mkdir(parents=True, exist_ok=False)
    repository = JobRepository(run_dir / "agent.db")
    repository.initialize()
    try:
        if not args.resume:
            fixture_path = Path(__file__).resolve().parents[2] / "evaluations/unified-agent-v1/pages.json"
            pages = [BrowserPage(**page) for page in json.loads(fixture_path.read_text(encoding="utf-8"))]
            browser, seeds = await build_browser_components(
                request, repository=repository, demo_pages=pages,
                archive_root=run_dir / "source-archive" / run_id, run_id=run_id,
            )
            user = UserProfile(keyword=request.keyword, location=request.location, target_count=request.target_count,
                               skills=request.skills, seed_urls=seeds)
        workflow = WebTaskWorkflow(
            browser=browser, extractor=PageExtractor(), verifier=JobVerifier(), matcher=JobMatcher(),
            repository=repository, reporter=MarkdownReporter(run_dir / "reports"), enforce_search_conditions=True,
        )
        services = AgentServices(workflow=workflow, run_dir=run_dir)
        if args.resume:
            result = await resume_agent_run(
                run_id=run_id, owner_id="acceptance", services=services,
                approval_decision=ApprovalDecision(approval_id=args.approval_id, outcome=args.resume),
            )
            for key in ("source_manifest", "source_mode", "source_captured_at"):
                if key in previous:
                    result.view.metadata.setdefault(key, previous[key])
        else:
            result = await start_agent_run(
                user,
                run_id=run_id,
                owner_id="acceptance",
                services=services,
                options=options,
            )
        view = result.view
        action_plan = ActionPlanWriter(run_dir / "action-plans").write_plan(
            run_id=run_id, user=user, jobs=view.jobs, matches=view.matches)
        dashboard = HtmlDashboard(run_dir / "dashboards").write_dashboard(
            user=user, jobs=view.jobs, matches=view.matches, metrics=view.metrics,
            execution_trace=view.metadata.get("execution_trace", []), orchestration_mode="hybrid",
            artifact_links={"报告": view.report_path, "行动计划": action_plan},
            source_metrics=view.metadata.get("source_metrics"),
            source_evidence=view.metadata.get("source_evidence"),
        )
        write_json(run_dir / "result.json", view.model_dump(mode="json"))
        summary = {"run_id": run_id, "engine": "hybrid", "planner_strategy": options.planner_strategy,
                   "reader_strategy": "rules", "status": result.status,
                   "terminal_reason": view.metadata.get("terminal_reason"), "valid_jobs": len(view.jobs),
                   "saved_jobs": len(repository.list_jobs()), "approval": result.approval,
                   "source_manifest": view.metadata.get("source_manifest"),
                   "source_mode": view.metadata.get("source_mode"),
                   "source_captured_at": view.metadata.get("source_captured_at"),
                   "source_metrics": view.metadata.get("source_metrics", {}),
                   "source_evidence": view.metadata.get("source_evidence", {}),
                   "conditions": {
                       "keyword": user.keyword, "location": user.location, "target_count": user.target_count},
                   "artifacts": {"report": str(view.report_path), "dashboard": str(dashboard),
                                 "action_plan": str(action_plan)}}
        write_json(run_dir / "summary.json", summary)
        print(
            json.dumps(
                {key: summary[key] for key in ("run_id", "status", "valid_jobs", "saved_jobs")},
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as error:
        if "browser" in locals() and hasattr(browser, "finalize"):
            browser.finalize()
        write_json(run_dir / "failure.json", {"run_id": run_id, "status": "failed", "error_type": type(error).__name__})
        raise


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_arguments(parser, args)
    try:
        return asyncio.run(run_acceptance(args))
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(2, f"Acceptance failed: {type(error).__name__}\n")


if __name__ == "__main__":
    raise SystemExit(main())
