from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv

from web_task_agent import __version__

# Load .env before anything else reads os.environ
# __file__ = .../Agent/src/web_task_agent/cli.py → parents[2] = .../Agent
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
from web_task_agent.action_plan import ActionPlanWriter
from web_task_agent.agent_approval import (
    ApprovalDecision,
    ApprovalOutcome,
    HitlRunStatus,
    HitlRuntimeError,
)
from web_task_agent.agent_checkpoint import open_sqlite_checkpointer
from web_task_agent.agent_cli import build_hybrid_runtime, write_hybrid_artifacts
from web_task_agent.agent_planner import build_configured_agent_planner
from web_task_agent.agent_planner_benchmark import (
    parse_planner_benchmark_providers,
    run_planner_benchmark,
    write_planner_benchmark_artifacts,
)
from web_task_agent.benchmark import (
    BenchmarkProviderResult,
    build_real_site_benchmark_v2_cases,
    parse_benchmark_providers,
    run_benchmark_matrix,
    write_benchmark_artifacts,
)
from web_task_agent.benchmark_explainer import (
    generate_benchmark_insights,
    write_benchmark_explanation_artifact,
)
from web_task_agent.browser import (
    BrowserConfigurationError,
    HttpPageLoader,
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
    build_real_site_sample_tasks,
    build_real_smoke_tasks,
)
from web_task_agent.extractor import PageExtractor
from web_task_agent.graph_export import LangGraphExporter
from web_task_agent.llm_extractor import DemoLlmFieldExtractor
from web_task_agent.llm_extractor import (
    LlmExtractorConfigurationError,
    build_configured_llm_field_extractor,
)
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import MatchResult, RunMetrics, UserProfile
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.skill_gap import summarize_skill_gaps
from web_task_agent.site_fixtures import PUBLIC_JOB_FIXTURE_PAGES
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.visual_extractor import DemoVisualJobExtractor
from web_task_agent.visual_provider import (
    VisualProviderConfigurationError,
    build_configured_visual_extractor,
)
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
        "--hybrid-agent",
        action="store_true",
        help="Run the bounded decision/tool/recovery Agent runtime.",
    )
    parser.add_argument(
        "--agent-max-steps",
        type=int,
        default=12,
        help="Maximum non-terminal tool steps for the hybrid Agent.",
    )
    parser.add_argument(
        "--hitl",
        action="store_true",
        help="Pause the Hybrid Agent before persisting results.",
    )
    parser.add_argument("--thread-id", help="Stable checkpoint thread identifier.")
    parser.add_argument(
        "--checkpoint-db",
        default=".agent/checkpoints.sqlite",
        help="SQLite path for LangGraph checkpoints.",
    )
    parser.add_argument(
        "--resume-approval",
        choices=["approve", "reject"],
        help="Resume a pending HITL thread with one approval outcome.",
    )
    parser.add_argument("--approval-id", help="Pending approval identifier to resume.")
    parser.add_argument(
        "--approval-note",
        default="",
        help="Public audit note attached to the approval decision.",
    )
    parser.add_argument(
        "--agent-planner-provider",
        choices=["deepseek", "qwen"],
        help="Optional structured LLM planner; deterministic policy is the fallback.",
    )
    parser.add_argument(
        "--agent-planner-model",
        help="Override the hybrid Agent planner model.",
    )
    parser.add_argument(
        "--agent-planner-benchmark",
        action="store_true",
        help="Compare deterministic, DeepSeek, and Qwen planners on controlled scenarios.",
    )
    parser.add_argument(
        "--agent-planner-benchmark-providers",
        default="deterministic,deepseek,qwen",
        help="Comma-separated planner benchmark providers.",
    )
    parser.add_argument(
        "--agent-planner-benchmark-output-dir",
        default="docs/results/planner-benchmark",
        help="Directory for planner benchmark JSON and Markdown artifacts.",
    )
    parser.add_argument(
        "--llm-extractor-demo",
        action="store_true",
        help="Use a deterministic LLM-style structured extractor demo.",
    )
    parser.add_argument(
        "--llm-extractor-provider",
        choices=["deepseek", "qwen"],
        help="Use a configured external LLM extractor provider for low-confidence pages.",
    )
    parser.add_argument(
        "--llm-extractor-model",
        help="Override the default provider model, such as deepseek-v4-flash or qwen-plus.",
    )
    parser.add_argument(
        "--llm-match",
        action="store_true",
        help="Enable LLM semantic matching for low rule-match-score jobs.",
    )
    parser.add_argument(
        "--llm-match-provider",
        choices=["deepseek", "qwen"],
        help="Use a configured external LLM provider for semantic matching.",
    )
    parser.add_argument(
        "--llm-match-model",
        help="Override the default model for LLM matching.",
    )
    parser.add_argument(
        "--llm-match-demo",
        action="store_true",
        help="Use a deterministic LLM-style semantic matching demo.",
    )
    parser.add_argument(
        "--visual-extractor-demo",
        action="store_true",
        help="Use deterministic screenshot-style visual job extraction for seed URL experiments.",
    )
    parser.add_argument(
        "--visual-extractor-provider",
        choices=["qwen-vl"],
        help="Use a configured external visual extractor provider (requires visual-web-agent).",
    )
    parser.add_argument(
        "--visual-extractor-model",
        help="Override the visual provider model, such as qwen-vl-plus.",
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
        "--real-site-sample",
        action="store_true",
        help="Use a small set of real job URLs when --evaluate or --compare-llm-extractor is enabled.",
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
    parser.add_argument(
        "--compare-llm-match",
        action="store_true",
        help="Compare rule matching with LLM semantic matching on real-site-sample jobs.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Run in interactive multi-turn mode: adjust search parameters and re-run.",
    )
    parser.add_argument(
        "--benchmark-v2",
        action="store_true",
        help="Run the real-site benchmark v2 provider matrix.",
    )
    parser.add_argument(
        "--benchmark-providers",
        default="baseline,llm-demo",
        help="Comma-separated providers: baseline,llm-demo,deepseek,qwen,qwen-vl.",
    )
    parser.add_argument(
        "--benchmark-limit",
        type=int,
        default=8,
        help="Limit benchmark v2 cases.",
    )
    parser.add_argument(
        "--benchmark-dashboard",
        action="store_true",
        help="Write a benchmark v2 HTML summary.",
    )
    parser.add_argument(
        "--benchmark-explain",
        action="store_true",
        help="Write a Chinese explanation artifact for benchmark v2 results.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(raw_argv)
    args._supplied_options = {
        token.partition("=")[0] for token in raw_argv if token.startswith("--")
    }
    return asyncio.run(_run(args))


def build_browser(*, demo: bool) -> FakeBrowserClient | BrowserUseClient:
    return FakeBrowserClient(DEMO_JOB_PAGES) if demo else BrowserUseClient()


async def _run(args: argparse.Namespace) -> int:
    hitl_error = validate_hitl_args(args)
    if hitl_error:
        print(f"HITL configuration error: {hitl_error}")
        return 2

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

    if args.agent_planner_benchmark:
        try:
            providers = parse_planner_benchmark_providers(
                args.agent_planner_benchmark_providers
            )
        except ValueError as exc:
            print(str(exc))
            return 2
        matrix = await run_planner_benchmark(
            providers=providers,
            output_dir=args.agent_planner_benchmark_output_dir,
        )
        artifacts = write_planner_benchmark_artifacts(
            matrix,
            args.agent_planner_benchmark_output_dir,
        )
        print("Planner benchmark")
        for provider in matrix.providers:
            print(
                f"{provider.provider}: {provider.status} "
                f"completion={provider.completed_cases}/{provider.total_cases} "
                f"termination={provider.terminated_cases}/{provider.total_cases} "
                f"fallback={provider.fallback_rate:.2f} "
                f"tokens={provider.total_tokens}"
            )
            if provider.error:
                print(f"  error: {provider.error}")
        for name, path in artifacts.items():
            print(f"{name.title()} written to: {path}")
        return 0 if any(item.status == "executed" for item in matrix.providers) else 2

    if args.compare_llm_extractor:
        try:
            result = await run_llm_extractor_comparison(args)
        except (LlmExtractorConfigurationError, VisualProviderConfigurationError) as exc:
            err_type = "LLM extractor" if isinstance(exc, LlmExtractorConfigurationError) else "Visual extractor"
            print(f"{err_type} is not configured: {exc}")
            return 2
        print("LLM extractor comparison")
        print(
            "baseline: "
            f"{result['baseline']['completed_tasks']}/{result['baseline']['total_tasks']}"
        )
        print(
            "llm-demo: "
            f"{result['llm_demo']['completed_tasks']}/{result['llm_demo']['total_tasks']}"
        )
        if args.llm_extractor_provider:
            provider_result = result[args.llm_extractor_provider]
            print(
                f"{args.llm_extractor_provider}: "
                f"{provider_result['completed_tasks']}/{provider_result['total_tasks']}"
            )
        if args.visual_extractor_demo:
            visual_result = result["visual_demo"]
            print(
                f"visual-demo: "
                f"{visual_result['completed_tasks']}/{visual_result['total_tasks']}"
            )
        if args.visual_extractor_provider:
            provider_result = result[args.visual_extractor_provider]
            print(
                f"{args.visual_extractor_provider}: "
                f"{provider_result['completed_tasks']}/{provider_result['total_tasks']}"
            )
        print(f"Comparison report written to: {result['report_path']}")
        if args.json_output:
            json_path = write_mapping_json_output(result, args.json_output)
            print(f"Comparison JSON written to: {json_path}")
        return 0

    if args.compare_llm_match:
        try:
            llm_matcher = build_cli_llm_matcher(args)
        except LlmExtractorConfigurationError as exc:
            print(f"LLM matcher is not configured: {exc}")
            return 2
        result = await run_llm_matcher_comparison(args, llm_matcher=llm_matcher)
        print("LLM match comparison")
        print(
            "rule-demo: "
            f"{result['rule_demo']['score_diff_count']}/{result['rule_demo']['total_pairs']} scores changed"
        )
        if result.get("llm_provider"):
            provider_result = result["llm_provider"]
            print(
                f"{args.llm_match_provider}: "
                f"{provider_result['score_diff_count']}/{provider_result['total_pairs']} scores changed"
            )
        print(f"Comparison report written to: {result['report_path']}")
        if args.json_output:
            json_path = write_mapping_json_output(result, args.json_output)
            print(f"Comparison JSON written to: {json_path}")
        return 0

    if args.benchmark_v2:
        try:
            providers = parse_benchmark_providers(args.benchmark_providers)
        except ValueError as exc:
            print(str(exc))
            return 2
        try:
            result = await run_cli_benchmark_v2(
                args, providers=providers, explain=args.benchmark_explain
            )
        except (LlmExtractorConfigurationError, VisualProviderConfigurationError) as exc:
            err_type = (
                "LLM extractor"
                if isinstance(exc, LlmExtractorConfigurationError)
                else "Visual extractor"
            )
            print(f"Benchmark {err_type} is not configured: {exc}")
            return 2
        print("Real site benchmark v2")
        for provider in result.providers:
            print(
                f"{provider.provider}: "
                f"{provider.completed_tasks}/{provider.total_tasks} "
                f"success_rate={provider.success_rate:.2f}"
            )
        if args.benchmark_explain:
            insight = generate_benchmark_insights(result)
            explanation_path = write_benchmark_explanation_artifact(
                result=result,
                insight=insight,
                output_dir=args.evaluation_dir,
            )
            print(f"Benchmark explanation written to: {explanation_path}")
        return 0

    if args.history:
        repo = JobRepository(args.db_path)
        repo.initialize()
        print_run_history(repo.list_run_metrics(limit=args.history_limit))
        return 0

    if args.export_graph:
        try:
            llm_field_extractor = build_cli_llm_field_extractor(args)
        except LlmExtractorConfigurationError as exc:
            print(f"LLM extractor is not configured: {exc}")
            return 2
        workflow = build_workflow(
            browser=FakeBrowserClient(DEMO_JOB_PAGES),
            db_path=args.db_path,
            report_dir=args.report_dir,
            llm_field_extractor=llm_field_extractor,
        )
        graph_path = LangGraphExporter().write_markdown(workflow)
        print(f"Graph written to: {graph_path}")
        return 0

    if args.evaluate:
        try:
            build_cli_llm_field_extractor(args)
        except LlmExtractorConfigurationError as exc:
            print(f"LLM extractor is not configured: {exc}")
            return 2
        try:
            resume_text = load_resume_text(args.resume_text, args.resume_file)
        except FileNotFoundError as exc:
            print(f"Resume file not found: {exc.filename}")
            return 2
        if args.real_site_sample:
            tasks = build_real_site_sample_tasks()[: args.evaluation_count]
            runner = EvaluationRunner(
                args.evaluation_dir,
                browser_factory=lambda task: BrowserUseClient(
                    page_loader=HttpPageLoader()
                ),
                extractor_factory=build_extractor_factory(args),
            )
        elif args.seed_url:
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

    if args.interactive:
        if not args.keyword and not args.seed_url:
            print("--keyword is required for --interactive mode.")
            return 2
        return await run_interactive(args)

    if (
        not args.keyword
        and not args.seed_url
        and not (args.hitl and args.resume_approval)
    ):
        print("--keyword is required unless --evaluate is used.")
        return 2

    if args.demo and args.visual_extractor_provider:
        print(
            "Error: --demo and --visual-extractor-provider cannot be used together.\n"
            "The visual provider uses its own Playwright browser to fetch real URLs;\n"
            "demo pages are fake URLs that a real browser cannot reach.\n"
            "Use --visual-extractor-demo for deterministic demo fixtures, or\n"
            "remove --demo and use --visual-extractor-provider with real seed URLs."
        )
        return 2
    if args.visual_extractor_demo and not args.seed_url:
        print(
            "Warning: --visual-extractor-demo is intended for use with --seed-url. "
            "Without seed URLs the visual extractor may have no matching fixtures "
            "and will fall back to text extraction."
        )
    if args.visual_extractor_provider and not args.seed_url:
        print(
            "Warning: --visual-extractor-provider requires --seed-url. "
            "The visual provider fetches pages on its own and cannot be used "
            "in search mode."
        )
    browser = build_browser(demo=args.demo)
    try:
        llm_field_extractor = build_cli_llm_field_extractor(args)
        llm_matcher = build_cli_llm_matcher(args)
    except LlmExtractorConfigurationError as exc:
        print(f"LLM extractor is not configured: {exc}")
        return 2
    try:
        visual_extractor = build_cli_visual_extractor(args)
    except VisualProviderConfigurationError as exc:
        print(f"Visual extractor is not configured: {exc}")
        return 2
    workflow = build_workflow(
        browser=browser,
        db_path=args.db_path,
        report_dir=args.report_dir,
        llm_field_extractor=llm_field_extractor,
        llm_matcher=llm_matcher,
        visual_extractor=visual_extractor,
    )
    if llm_matcher is not None:
        mode = f"llm-match-{args.llm_match_provider or 'demo'}"
        print(f"LLM match enabled: {mode}")
    try:
        if args.hybrid_agent and args.hitl:
            planner = None
            if args.agent_planner_provider and not args.resume_approval:
                planner = build_configured_agent_planner(
                    provider=args.agent_planner_provider,
                    model=args.agent_planner_model,
                )
            async with open_sqlite_checkpointer(args.checkpoint_db) as saver:
                runtime = build_hybrid_runtime(
                    workflow,
                    planner=planner,
                    checkpointer=saver,
                )
                if args.resume_approval:
                    hitl_result = await runtime.resume_hitl(
                        thread_id=args.thread_id,
                        decision=ApprovalDecision(
                            approval_id=args.approval_id,
                            outcome=ApprovalOutcome(args.resume_approval),
                            note=args.approval_note,
                        ),
                    )
                else:
                    resume_text = load_resume_text(args.resume_text, args.resume_file)
                    user = UserProfile(
                        keyword=args.keyword or "seed URLs",
                        location=args.location,
                        target_count=args.target_count,
                        skills=args.skill,
                        resume_text=resume_text,
                        seed_urls=args.seed_url,
                    )
                    hitl_result = await workflow.start_with_hybrid_agent_hitl(
                        user,
                        runtime=runtime,
                        thread_id=args.thread_id,
                        max_steps=args.agent_max_steps,
                    )
            agent_state = hitl_result.state
            artifacts = write_hybrid_artifacts(
                agent_state,
                report_dir=args.report_dir,
                dashboard_dir=args.dashboard_dir,
                write_dashboard=args.dashboard,
                json_output=args.json_output,
            )
            print("Hybrid Decision Agent HITL: enabled")
            print(f"HITL status: {hitl_result.status.value}")
            print(f"Thread ID: {args.thread_id}")
            print(f"Terminal reason: {agent_state.terminal_reason}")
            print(f"Verified jobs: {len(agent_state.verified_jobs)}")
            if hitl_result.status is HitlRunStatus.AWAITING_APPROVAL:
                approval = hitl_result.approval
                if approval is None:
                    raise HitlRuntimeError("paused run has no approval request")
                print(f"Approval ID: {approval.approval_id}")
                print(f"Pending action: {approval.action}")
                print(f"Summary: {approval.summary}")
                base = (
                    "web-task-agent --hybrid-agent --hitl "
                    f"--thread-id {args.thread_id} "
                    f'--checkpoint-db "{args.checkpoint_db}" '
                    f'--db-path "{args.db_path}" '
                    f"--approval-id {approval.approval_id}"
                )
                print(f"Approve: {base} --resume-approval approve")
                print(f"Reject: {base} --resume-approval reject")
            for artifact_name, artifact_path in artifacts.items():
                print(f"{artifact_name.title()} written to: {artifact_path}")
            return (
                0
                if hitl_result.status
                in {
                    HitlRunStatus.AWAITING_APPROVAL,
                    HitlRunStatus.COMPLETED,
                    HitlRunStatus.REJECTED,
                }
                else 2
            )

        resume_text = load_resume_text(args.resume_text, args.resume_file)
        user = UserProfile(
            keyword=args.keyword or "seed URLs",
            location=args.location,
            target_count=args.target_count,
            skills=args.skill,
            resume_text=resume_text,
            seed_urls=args.seed_url,
        )
        if args.hybrid_agent:
            planner = None
            if args.agent_planner_provider:
                planner = build_configured_agent_planner(
                    provider=args.agent_planner_provider,
                    model=args.agent_planner_model,
                )
            runtime = build_hybrid_runtime(workflow, planner=planner)
            agent_state = await workflow.run_with_hybrid_agent(
                user,
                runtime=runtime,
                max_steps=args.agent_max_steps,
            )
            artifacts = write_hybrid_artifacts(
                agent_state,
                report_dir=args.report_dir,
                dashboard_dir=args.dashboard_dir,
                write_dashboard=args.dashboard,
                json_output=args.json_output,
            )
            print("Hybrid Decision Agent: enabled")
            print(f"Terminal status: {agent_state.terminal_status}")
            print(f"Terminal reason: {agent_state.terminal_reason}")
            print(f"Verified jobs: {len(agent_state.verified_jobs)}")
            for artifact_name, artifact_path in artifacts.items():
                print(f"{artifact_name.title()} written to: {artifact_path}")
            return 0 if agent_state.terminal_status == "completed" else 2
        if args.langgraph:
            state = await workflow.run_with_langgraph(user)
        else:
            state = await workflow.run(user)
    except FileNotFoundError as exc:
        print(f"Resume file not found: {exc.filename}")
        return 2
    except HitlRuntimeError as exc:
        print(f"HITL runtime error: {exc}")
        return 2
    except OSError as exc:
        print(f"HITL checkpoint error: {type(exc).__name__}: {exc}")
        return 2
    except BrowserConfigurationError as exc:
        print(f"Real browser-use mode is not configured: {exc}")
        print("Use --demo for the deterministic local demo path.")
        return 2
    finally:
        if visual_extractor is not None and hasattr(visual_extractor, "close"):
            await visual_extractor.close()
    valid_jobs = state.metrics.valid_jobs if state.metrics else 0
    if args.langgraph:
        print("LangGraph workflow: enabled")
    if args.llm_extractor_demo:
        print("LLM extractor demo: enabled")
        state.metadata["extractor_mode"] = "llm-demo"
    if args.llm_extractor_provider:
        model = getattr(llm_field_extractor, "model", args.llm_extractor_model or "")
        print(f"LLM extractor provider: {args.llm_extractor_provider}")
        state.metadata["extractor_mode"] = "llm-provider"
        state.metadata["llm_provider"] = args.llm_extractor_provider
        state.metadata["llm_model"] = model
    if args.visual_extractor_demo:
        print("Visual extractor demo: enabled")
        state.metadata["extractor_mode"] = "visual-demo"
    if args.visual_extractor_provider:
        model = getattr(visual_extractor, "model", args.visual_extractor_model or "")
        print(f"Visual extractor provider: {args.visual_extractor_provider}")
        state.metadata["extractor_mode"] = "visual-provider"
        state.metadata["visual_provider"] = args.visual_extractor_provider
        state.metadata["visual_model"] = model
    print(f"Report written to: {state.report_path}")
    print(f"Valid jobs: {valid_jobs}")
    if args.visual_extractor_provider and valid_jobs == 0:
        _print_visual_provider_diagnostics(state)
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
                orchestration_mode=state.metadata.get("orchestration_mode", "sequential"),
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
            orchestration_mode=state.metadata.get("orchestration_mode", "sequential"),
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
                orchestration_mode=state.metadata.get("orchestration_mode", "sequential"),
            )
        )
        print(f"Dashboard written to: {dashboard_path}")
    if args.json_output:
        json_path = write_json_output(state, args.json_output)
        print(f"JSON output written to: {json_path}")
    if _visual_provider_run_failed(args, valid_jobs):
        print(
            "Visual provider produced no valid jobs. "
            "Treating this provider smoke run as failed; "
            "inspect diagnostics and JSON output."
        )
        return 2
    return 0


def validate_hitl_args(args: argparse.Namespace) -> str | None:
    if args.hitl and not args.hybrid_agent:
        return "--hitl requires --hybrid-agent"
    if args.hitl and not str(args.thread_id or "").strip():
        return "--hitl requires --thread-id"
    if args.resume_approval and not args.hitl:
        return "--resume-approval requires --hitl"
    if args.resume_approval and not str(args.approval_id or "").strip():
        return "--resume-approval requires --approval-id"
    if args.approval_id and not args.resume_approval:
        return "--approval-id requires --resume-approval"
    if args.approval_note and not args.resume_approval:
        return "--approval-note requires --resume-approval"
    if args.resume_approval:
        supplied = getattr(args, "_supplied_options", set())
        initial_only = (
            "--keyword",
            "--location",
            "--target-count",
            "--skill",
            "--seed-url",
            "--resume-text",
            "--resume-file",
            "--agent-max-steps",
            "--agent-planner-provider",
            "--agent-planner-model",
        )
        for option in initial_only:
            if option in supplied:
                return f"{option} cannot be used when resuming a HITL thread"
    return None


def _visual_provider_run_failed(args: argparse.Namespace, valid_jobs: int) -> bool:
    """Real provider smoke run that produced zero valid jobs should fail.

    Comparison and evaluation paths are excluded — their purpose is
    side-by-side measurement, not single-provider validation.
    """
    return (
        bool(args.visual_extractor_provider)
        and not args.compare_llm_extractor
        and not args.evaluate
        and valid_jobs == 0
    )


# ── Interactive multi-turn mode ───────────────────────────────────────


async def run_interactive(args: argparse.Namespace) -> int:
    """Multi-turn interactive Agent: adjust search and iterate based on results.

    Commands:
      more / more:N    → increase target count (default +5)
      skill:X          → add a skill tag
      keyword:X        → change search keyword
      location:X       → change location
      status           → show current round summary
      done / quit      → finish and write consolidated report
    """
    from uuid import uuid4

    if args.demo and args.visual_extractor_provider:
        print(
            "Error: --demo and --visual-extractor-provider cannot be used together.\n"
            "The visual provider uses its own Playwright browser to fetch real URLs."
        )
        return 2

    print("=== Web Task Agent — Interactive Mode ===")
    print("Commands: more | more:N | skill:X | keyword:X | location:X | status | done")
    print()

    # ── Setup ──
    browser = build_browser(demo=args.demo)
    try:
        llm_field_extractor = build_cli_llm_field_extractor(args)
        llm_matcher = build_cli_llm_matcher(args)
    except LlmExtractorConfigurationError as exc:
        print(f"LLM config error: {exc}")
        return 2

    try:
        visual_extractor = build_cli_visual_extractor(args)
    except VisualProviderConfigurationError as exc:
        print(f"Visual extractor is not configured: {exc}")
        return 2
    workflow = build_workflow(
        browser=browser,
        db_path=args.db_path,
        report_dir=args.report_dir,
        llm_field_extractor=llm_field_extractor,
        llm_matcher=llm_matcher,
        visual_extractor=visual_extractor,
    )
    try:
        resume_text = load_resume_text(args.resume_text, args.resume_file)
    except FileNotFoundError as exc:
        print(f"Resume file not found: {exc.filename}")
        return 2

    # Accumulated state across rounds
    all_jobs: list[JobPosting] = []
    all_matches: list[MatchResult] = []
    round_states: list[WorkflowState] = []
    round_num = 0
    keyword = args.keyword
    location = args.location
    target_count = args.target_count
    skills = list(args.skill)
    seed_urls = list(args.seed_url)

    # ── Main loop ──
    while True:
        round_num += 1
        print(f"--- Round {round_num} ---")
        print(f"  Keyword: {keyword}, Location: {location}, Target: {target_count}")
        if skills:
            print(f"  Skills: {', '.join(skills)}")

        user = UserProfile(
            keyword=keyword,
            location=location,
            target_count=target_count,
            skills=skills,
            resume_text=resume_text,
            seed_urls=seed_urls,
        )

        try:
            state = await workflow.run(user, run_id=f"interactive-{round_num:02d}-{uuid4().hex[:6]}")
        except BrowserConfigurationError as exc:
            print(f"  Browser error: {exc}")
            print("  Try --demo for deterministic local pages.")
            if visual_extractor is not None and hasattr(visual_extractor, "close"):
                await visual_extractor.close()
            return 2

        round_states.append(state)
        valid = state.metrics.valid_jobs if state.metrics else 0
        print(f"  Jobs found: {valid} valid / {state.metrics.jobs_found if state.metrics else 0} total")

        # Collect jobs (dedupe by URL)
        seen_urls = {j.url for j in all_jobs}
        for job in state.jobs:
            if job.url not in seen_urls:
                seen_urls.add(job.url)
                all_jobs.append(job)
        all_matches.extend(state.matches)

        # ── REPL ──
        cmd = input("\n> ").strip()
        if not cmd:
            continue

        if cmd.lower() in ("done", "quit", "q"):
            break

        if cmd.lower() == "status":
            print(f"  Total unique jobs: {len(all_jobs)} across {round_num} rounds")
            if all_matches:
                gaps = summarize_skill_gaps(all_matches)
                if gaps:
                    print(f"  Top skill gaps: {', '.join(f'{s}({c})' for s, c in gaps[:5])}")
            continue

        if cmd.lower().startswith("more"):
            parts = cmd.split(":", 1)
            increment = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 5
            target_count += increment
            print(f"  Target → {target_count}")
            continue

        if cmd.lower().startswith("skill:"):
            new_skill = cmd.split(":", 1)[1].strip()
            if new_skill and new_skill.casefold() not in {s.casefold() for s in skills}:
                skills.append(new_skill)
                print(f"  Skills → {', '.join(skills)}")
            continue

        if cmd.lower().startswith("keyword:"):
            keyword = cmd.split(":", 1)[1].strip()
            if keyword:
                print(f"  Keyword → {keyword}")
            continue

        if cmd.lower().startswith("location:"):
            location = cmd.split(":", 1)[1].strip()
            if location:
                print(f"  Location → {location}")
            continue

        print(f"  Unknown command: {cmd}")
        print("  Commands: more | more:N | skill:X | keyword:X | location:X | status | done")

    # ── Consolidated report ──
    print(f"\n=== Search complete: {len(all_jobs)} unique jobs across {round_num} rounds ===")

    if all_jobs and state.metrics:
        # Build a synthetic metrics for the consolidated report
        from datetime import datetime, timezone
        cons_metrics = RunMetrics(
            run_id=f"interactive-consolidated-{uuid4().hex[:8]}",
            pages_visited=sum((s.metrics.pages_visited if s.metrics else 0) for s in round_states),
            jobs_found=sum((s.metrics.jobs_found if s.metrics else 0) for s in round_states),
            valid_jobs=len(all_jobs),
            duplicate_jobs=sum((s.metrics.duplicate_jobs if s.metrics else 0) for s in round_states),
            failed_pages=sum(len(s.failed_urls) for s in round_states),
            avg_steps_per_job=round(
                sum((s.metrics.pages_visited if s.metrics else 0) for s in round_states) / len(all_jobs), 2
            ) if all_jobs else 0.0,
            finished_at=datetime.now(timezone.utc),
        )
        state.metrics = cons_metrics
        state.jobs = all_jobs
        state.matches = all_matches
        state.metadata["interactive_rounds"] = round_num
        state.metadata["execution_trace"].append(
            {"node": "interactive", "summary": f"consolidated {round_num} rounds, {len(all_jobs)} unique jobs"}
        )

        # Reuse reporter/dashboard/action-plan pipeline
        artifact_links = {}
        if args.action_plan and state.metrics:
            plan_path = ActionPlanWriter(args.action_plan_dir).write_plan(
                run_id=state.metrics.run_id,
                user=UserProfile(keyword=keyword, location=location, target_count=target_count, skills=skills, resume_text=resume_text),
                jobs=all_jobs,
                matches=all_matches,
            )
            state.metadata["action_plan_path"] = plan_path.as_posix()
            artifact_links["行动计划"] = plan_path
            print(f"Action plan written to: {plan_path}")
            print(f"Top action gaps: {', '.join(f'{s}({c})' for s, c in summarize_skill_gaps(all_matches)[:3])}")

        if args.dashboard and state.metrics:
            dashboard_path = HtmlDashboard(args.dashboard_dir).write_dashboard(
                user=UserProfile(keyword=keyword, location=location, skills=skills, resume_text=resume_text),
                jobs=all_jobs,
                matches=all_matches,
                metrics=cons_metrics,
                search_queries=[],
                failed_url_errors=state.metadata.get("failed_url_errors", []),
                artifact_links=artifact_links,
                execution_trace=state.metadata.get("execution_trace", []),
                orchestration_mode="interactive",
            )
            state.metadata["dashboard_path"] = dashboard_path.as_posix()
            print(f"Dashboard written to: {dashboard_path}")

        report_path = workflow.reporter.write_report(
            user=UserProfile(keyword=keyword, location=location, skills=skills, resume_text=resume_text),
            jobs=all_jobs,
            matches=all_matches,
            metrics=cons_metrics,
            artifact_links=artifact_links,
            execution_trace=state.metadata.get("execution_trace", []),
            orchestration_mode="interactive",
        )
        print(f"Consolidated report: {report_path}")

        if args.json_output:
            json_path = write_json_output(state, args.json_output)
            print(f"JSON written to: {json_path}")

    if visual_extractor is not None and hasattr(visual_extractor, "close"):
        await visual_extractor.close()
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


async def run_cli_benchmark_v2(
    args: argparse.Namespace,
    *,
    providers: list[str],
    explain: bool = False,
) -> BenchmarkMatrixResult:
    """Run the benchmark v2 provider matrix from CLI configuration."""
    cases = build_real_site_benchmark_v2_cases()[: args.benchmark_limit]

    async def run_provider(provider, tasks, output_dir, cli_args):
        start = perf_counter()
        if provider == "baseline":
            eval_result = await EvaluationRunner(
                output_dir,
                browser_factory=lambda task: BrowserUseClient(
                    page_loader=HttpPageLoader()
                ),
            ).run(tasks=tasks)
        elif provider == "llm-demo":
            eval_result = await EvaluationRunner(
                output_dir,
                browser_factory=lambda task: BrowserUseClient(
                    page_loader=HttpPageLoader()
                ),
                extractor_factory=lambda task: PageExtractor(
                    llm_field_extractor=DemoLlmFieldExtractor()
                ),
            ).run(tasks=tasks)
        elif provider in {"deepseek", "qwen"}:
            provider_args = argparse.Namespace(**vars(cli_args))
            provider_args.llm_extractor_provider = provider
            eval_result = await EvaluationRunner(
                output_dir,
                browser_factory=lambda task: BrowserUseClient(
                    page_loader=HttpPageLoader()
                ),
                extractor_factory=lambda task: PageExtractor(
                    llm_field_extractor=build_cli_llm_field_extractor(provider_args)
                ),
            ).run(tasks=tasks)
        elif provider == "qwen-vl":
            visual_provider = build_configured_visual_extractor(
                provider="qwen-vl",
                model=cli_args.visual_extractor_model,
            )
            try:
                eval_result = await EvaluationRunner(
                    output_dir,
                    browser_factory=lambda task: BrowserUseClient(
                        page_loader=HttpPageLoader()
                    ),
                    extractor_factory=lambda task: PageExtractor(),
                    visual_extractor_factory=lambda task: visual_provider,
                ).run(tasks=tasks)
            finally:
                await visual_provider.close()
        else:
            raise ValueError(f"Unsupported benchmark provider: {provider}")
        return BenchmarkProviderResult.from_evaluation(
            provider=provider,
            result=eval_result,
            elapsed_seconds=perf_counter() - start,
        )

    result = await run_benchmark_matrix(
        cases=cases,
        providers=providers,
        output_dir=args.evaluation_dir,
        args=args,
        run_provider=run_provider,
    )
    insight = generate_benchmark_insights(result) if explain else None
    json_path, md_path = write_benchmark_artifacts(
        result=result,
        output_dir=args.evaluation_dir,
    )
    print(f"Benchmark Markdown written to: {md_path}")
    print(f"Benchmark JSON written to: {json_path}")
    if args.benchmark_dashboard:
        dashboard_dir = HtmlDashboard(args.dashboard_dir).output_dir
        dashboard_dir.mkdir(parents=True, exist_ok=True)
        dashboard_path = dashboard_dir / "benchmark-v2.html"
        dashboard_path.write_text(
            HtmlDashboard(args.dashboard_dir).render_benchmark_summary(
                result, insight=insight
            ),
            encoding="utf-8",
        )
        print(f"Benchmark dashboard written to: {dashboard_path}")
    return result


def write_mapping_json_output(payload: dict, output_path: str) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


async def run_llm_extractor_comparison(args: argparse.Namespace) -> dict:
    if args.real_site_sample:
        tasks = build_real_site_sample_tasks()[: args.evaluation_count]
        seed_urls = [seed_url for task in tasks for seed_url in task.seed_urls]
        browser_factory = lambda task: BrowserUseClient(page_loader=HttpPageLoader())
    else:
        seed_urls = args.seed_url or ["https://example.com/jobs/unstructured-ai-agent-intern"]
        tasks = [
            EvaluationTask(
                keyword=args.keyword or "AI intern",
                location=args.location,
                target_count=1,
                skills=args.skill,
                seed_urls=[seed_url],
            )
            for seed_url in seed_urls
        ]
        browser_factory = None

    baseline = await EvaluationRunner(
        args.evaluation_dir,
        browser_factory=browser_factory,
    ).run(tasks=tasks)
    llm_demo = await EvaluationRunner(
        args.evaluation_dir,
        browser_factory=browser_factory,
        extractor_factory=lambda task: PageExtractor(
            llm_field_extractor=DemoLlmFieldExtractor(),
        ),
    ).run(tasks=tasks)
    extractors: dict[str, dict] = {
        "baseline": baseline.model_dump(mode="json"),
        "llm_demo": llm_demo.model_dump(mode="json"),
    }
    if args.visual_extractor_demo:
        visual_demo = await EvaluationRunner(
            args.evaluation_dir,
            browser_factory=browser_factory,
            extractor_factory=lambda task: PageExtractor(),
            visual_extractor_factory=lambda task: DemoVisualJobExtractor(),
        ).run(tasks=tasks)
        extractors["visual_demo"] = visual_demo.model_dump(mode="json")
    if args.visual_extractor_provider:
        try:
            provider = build_cli_visual_extractor(args)
        except VisualProviderConfigurationError as exc:
            print(f"Visual extractor is not configured: {exc}")
            raise
        try:
            provider_eval = await EvaluationRunner(
                args.evaluation_dir,
                browser_factory=browser_factory,
                extractor_factory=lambda task: PageExtractor(),
                visual_extractor_factory=lambda task: provider,
            ).run(tasks=tasks)
            extractors[args.visual_extractor_provider] = provider_eval.model_dump(mode="json")
        finally:
            if hasattr(provider, "close"):
                await provider.close()
    if args.llm_extractor_provider:
        provider_result = await EvaluationRunner(
            args.evaluation_dir,
            browser_factory=browser_factory,
            extractor_factory=lambda task: PageExtractor(
                llm_field_extractor=build_cli_llm_field_extractor(args),
            ),
        ).run(tasks=tasks)
        extractors[args.llm_extractor_provider] = provider_result.model_dump(mode="json")

    report_path = write_llm_comparison_report(
        output_dir=args.evaluation_dir,
        seed_urls=seed_urls,
        extractors=extractors,
    )
    result = {
        "seed_urls": seed_urls,
        "report_path": report_path.as_posix(),
        "extractors": extractors,
        "baseline": baseline.model_dump(mode="json"),
        "llm_demo": llm_demo.model_dump(mode="json"),
    }
    if args.llm_extractor_provider:
        result[args.llm_extractor_provider] = extractors[args.llm_extractor_provider]
    if args.visual_extractor_demo:
        result["visual_demo"] = extractors["visual_demo"]
    if args.visual_extractor_provider:
        result[args.visual_extractor_provider] = extractors[args.visual_extractor_provider]
    return result


async def run_llm_matcher_comparison(
    args: argparse.Namespace,
    *,
    llm_matcher,
) -> dict:
    """Compare rule matching with LLM semantic matching on real-site pages.

    Steps:
    1. Fetch and extract jobs from real URLs (browser + extractor).
    2. For each job, run rule match and LLM match side-by-side.
    3. Compare scores, matched/missing skills, priorities.
    4. Write a Markdown comparison report.
    """
    from web_task_agent.matcher import JobMatcher
    from web_task_agent.extractor import PageExtractor
    from web_task_agent.verifier import JobVerifier

    if args.real_site_sample:
        tasks = build_real_site_sample_tasks()[: args.evaluation_count]
        browser = BrowserUseClient(page_loader=HttpPageLoader())
    else:
        tasks = build_real_site_sample_tasks()  # always use real sites for meaningful comparison
        browser = BrowserUseClient(page_loader=HttpPageLoader())

    extractor = PageExtractor(
        llm_field_extractor=build_cli_llm_field_extractor(args),
    )
    verifier = JobVerifier(required_keywords=["AI", "LLM", "Agent", "analytics", "developer", "platform", "strategy", "deployment", "consultant", "director"])
    rule_matcher = JobMatcher()
    llm_matcher_instance = JobMatcher(llm_matcher=llm_matcher)

    try:
        resume_text = load_resume_text(args.resume_text, args.resume_file)
    except FileNotFoundError as exc:
        print(f"Resume file not found: {exc.filename}")
        resume_text = ""

    user = UserProfile(
        keyword=args.keyword or "AI intern",
        location=args.location,
        target_count=args.target_count,
        skills=args.skill or ["Python", "LangGraph"],
        resume_text=resume_text,
    )

    # ── Step 1: Fetch + extract jobs from each URL ──
    pairs: list[dict] = []
    for task in tasks:
        url = task.seed_urls[0]
        try:
            page = await browser.open_url(url)
            job = extractor.extract(page)
            if not verifier.verify(job).is_valid:
                continue
        except Exception as exc:
            # URL failed → skip this entry in comparison
            continue

        # ── Step 2: Rule match + LLM match ──
        rule_result = rule_matcher.match(user=user, job=job)
        llm_result = llm_matcher_instance.match(user=user, job=job)

        pairs.append({
            "url": url,
            "title": job.title,
            "company": job.company,
            "job_skills": job.skills,
            "rule": rule_result.model_dump(mode="json"),
            "llm": llm_result.model_dump(mode="json"),
        })

    # ── Step 3: Summarize ──
    result: dict[str, object] = {
        "total_pairs": len(pairs),
        "rule_demo": _summarize_match_comparison(pairs),
        "seed_urls": [t.seed_urls[0] for t in tasks],
        "pairs": pairs,
    }

    # ── Step 4: Write report ──
    report_path = write_llm_match_comparison_report(
        output_dir=args.evaluation_dir,
        pairs=pairs,
        llm_provider=args.llm_match_provider,
        args=args,
    )
    result["report_path"] = report_path.as_posix()
    return result


def _summarize_match_comparison(pairs: list[dict]) -> dict:
    """Count how many pairs had score differences between rule and LLM matching."""
    score_diff_count = 0
    priority_change_count = 0
    for pair in pairs:
        rule_score = pair["rule"]["score"]
        llm_score = pair["llm"]["score"]
        if abs(rule_score - llm_score) > 0.01:
            score_diff_count += 1
        if pair["rule"]["priority"] != pair["llm"]["priority"]:
            priority_change_count += 1
    return {
        "total_pairs": len(pairs),
        "score_diff_count": score_diff_count,
        "priority_change_count": priority_change_count,
    }


def write_llm_match_comparison_report(
    *,
    output_dir: str | Path,
    pairs: list[dict],
    llm_provider: str | None = None,
    args: argparse.Namespace | None = None,
) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "llm-match-comparison.md"

    matcher_label = llm_provider or "llm-demo"
    rule_demo = _summarize_match_comparison(pairs)

    lines = [
        "# LLM 语义匹配对比评测",
        "",
        "## 搜索条件",
        "",
        f"- 技能标签: {', '.join(args.skill) if args and args.skill else '未提供'}",
        f"- 岗位数: {len(pairs)}",
        f"- LLM 匹配器: {matcher_label}",
        "",
        "## 汇总",
        "",
        f"- 有效岗位-匹配对: {len(pairs)}",
        f"- 规则 vs {matcher_label}: {rule_demo['score_diff_count']}/{rule_demo['total_pairs']} 分数变化, {rule_demo['priority_change_count']} 优先级变化",
        "",
        "## 逐对明细",
        "",
        "| # | 岗位 | 公司 | 岗位技能 | 规则分 | LLM 分 | 规则优先级 | LLM 优先级 | 规则匹配 | LLM 匹配 |",
        "|---|---|---|---:|---:|---|---|---|---|",
    ]
    for i, pair in enumerate(pairs, start=1):
        rule = pair["rule"]
        llm = pair["llm"]
        lines.append(
            f"| {i} | {pair['title'][:30]} | {pair['company'][:20]} | "
            f"{', '.join(pair['job_skills'][:3]) if pair['job_skills'] else '-'} | "
            f"{rule['score']:.2f} | {llm['score']:.2f} | "
            f"{rule['priority']} | {llm['priority']} | "
            f"{', '.join(rule['matched_skills'][:3]) if rule['matched_skills'] else '-'} | "
            f"{', '.join(llm['matched_skills'][:3]) if llm['matched_skills'] else '-'} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_llm_comparison_report(
    *,
    output_dir: str | Path,
    seed_urls: list[str],
    extractors: dict[str, dict],
) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "llm-extractor-comparison.md"
    lines = [
        "# LLM 抽取器对比评测",
        "",
        "## Seed URLs",
        "",
    ]
    lines.extend(f"- {url}" for url in seed_urls)
    lines.extend(
        [
            "",
            "## 汇总",
            "",
            "| Extractor | Tasks | Completed | Success Rate | Valid Jobs | Failure Counts |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for name, result in extractors.items():
        failure_counts = result.get("failure_counts") or {}
        failure_summary = (
            ", ".join(f"{key}={value}" for key, value in sorted(failure_counts.items()))
            if failure_counts
            else "-"
        )
        lines.append(
            "| "
            f"{name} | {result.get('total_tasks', 0)} | "
            f"{result.get('completed_tasks', 0)} | "
            f"{result.get('success_rate', 0.0):.2f} | "
            f"{result.get('total_valid_jobs', 0)} | {failure_summary} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


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
            r'.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" '
            r"--target-count 2 --skill Python --skill LangGraph --demo "
            r"--langgraph --dashboard --json-output outputs\langgraph-result.json"
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
        (
            r'.\.venv\Scripts\web-task-agent.exe --seed-url '
            r'"https://example.com/jobs/unstructured-ai-agent-intern" --demo '
            r"--target-count 1 --llm-extractor-provider deepseek "
            r"--llm-extractor-model deepseek-v4-flash "
            r"--json-output outputs\deepseek-llm-demo.json"
        ),
        r".\.venv\Scripts\web-task-agent.exe --history",
        (
            r'.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" '
            r"--target-count 2 --skill Python --skill FastAPI "
            r"--resume-text \"Built REST APIs with FastAPI.\" "
            r"--demo --llm-match --json-output outputs\semantic-match.json"
        ),
        (
            r".\.venv\Scripts\web-task-agent.exe --compare-llm-extractor "
            r"--real-site-sample --evaluation-count 4 "
            r"--llm-extractor-provider deepseek "
            r"--json-output evaluations\final-comparison.json"
        ),
        (
            r".\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites "
            r"--json-output evaluations\fixture-result.json"
        ),
        (
            r'.\.venv\Scripts\web-task-agent.exe --seed-url '
            r'"https://example.com/jobs/visual-ai-intern" --demo '
            r"--target-count 1 --visual-extractor-demo "
            r"--json-output outputs\visual-demo.json"
        ),
        (
            r".\.venv\Scripts\web-task-agent.exe --compare-llm-extractor "
            r"--seed-url https://example.com/jobs/visual-ai-intern "
            r"--visual-extractor-demo "
            r"--json-output evaluations\visual-comparison.json"
        ),
        (
            r".\.venv\Scripts\web-task-agent.exe --seed-url "
            r'"https://job-boards.greenhouse.io/anthropic/jobs/5116927008" '
            r"--target-count 1 --visual-extractor-provider qwen-vl "
            r"--json-output outputs\visual-provider.json"
        ),
        (
            r".\.venv\Scripts\web-task-agent.exe --compare-llm-extractor "
            r"--real-site-sample --evaluation-count 4 "
            r"--visual-extractor-provider qwen-vl "
            r"--json-output evaluations\visual-provider-comparison.json"
        ),
        (
            r".\.venv\Scripts\web-task-agent.exe --benchmark-v2 "
            r"--benchmark-providers baseline,llm-demo,deepseek "
            r"--benchmark-limit 8 --benchmark-dashboard --benchmark-explain"
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
    llm_field_extractor=None,
    llm_matcher=None,
    visual_extractor=None,
) -> WebTaskWorkflow:
    repo = JobRepository(db_path)
    repo.initialize()
    return WebTaskWorkflow(
        browser=browser,
        extractor=PageExtractor(llm_field_extractor=llm_field_extractor),
        matcher=JobMatcher(llm_matcher=llm_matcher),
        verifier=JobVerifier(required_keywords=["AI", "LLM", "Agent"]),
        repository=repo,
        reporter=MarkdownReporter(report_dir),
        visual_extractor=visual_extractor,
    )


def build_extractor_factory(args: argparse.Namespace):
    if not args.llm_extractor_demo and not args.llm_extractor_provider:
        return None
    return lambda task: PageExtractor(
        llm_field_extractor=build_cli_llm_field_extractor(args)
    )


def build_cli_llm_field_extractor(args: argparse.Namespace):
    if args.llm_extractor_demo:
        return DemoLlmFieldExtractor()
    if args.llm_extractor_provider:
        return build_configured_llm_field_extractor(
            provider=args.llm_extractor_provider,
            model=args.llm_extractor_model,
        )
    return None


def build_cli_llm_matcher(args: argparse.Namespace):
    if args.llm_match_demo:
        from web_task_agent.llm_extractor import DemoLlmMatcher
        return DemoLlmMatcher()
    if args.llm_match_provider:
        from web_task_agent.llm_extractor import build_configured_llm_matcher
        return build_configured_llm_matcher(
            provider=args.llm_match_provider,
            model=args.llm_match_model,
        )
    if args.llm_match:
        # --llm-match without --llm-match-provider defaults to demo
        from web_task_agent.llm_extractor import DemoLlmMatcher
        return DemoLlmMatcher()
    return None


def _print_visual_provider_diagnostics(state) -> None:
    """Print diagnostic info when real visual provider produces no valid jobs."""
    visual_stats = state.metadata.get("visual_extraction", {})
    successes = visual_stats.get("successes", 0)
    failures = visual_stats.get("failures", 0)
    errors = visual_stats.get("errors", [])

    print("  Visual provider diagnostics:")
    print(f"    extraction attempts: {successes + failures}")
    print(f"    visual successes: {successes}")
    print(f"    visual failures (fell back to text): {failures}")

    if errors:
        for err in errors[:3]:
            print(f"    error: {err.get('url', '-')} → {err.get('error', '-')[:120]}")

    # Check if verifier filtered the extracted jobs
    filtered = state.metadata.get("filtered_jobs", [])
    if filtered:
        print(f"    verifier filtered {len(filtered)} jobs:")
        for item in filtered[:5]:
            reasons = ", ".join(item.get("reasons", []))
            print(
                f"      {item.get('title', '-')[:50]} @ {item.get('company', '-')[:30]}"
                f" → {reasons}"
            )

    # Check for empty extractions (visual "succeeded" but produced garbage)
    jobs_found = state.metadata.get("jobs_found", 0)
    if jobs_found == 0 and successes == 0 and failures > 0:
        print(
            "    All visual extractions failed and text fallback produced nothing. "
            "Check that the seed URLs are real, publicly accessible pages."
        )


def build_cli_visual_extractor(args: argparse.Namespace):
    """Build a visual extractor from CLI args.

    Priority: demo > provider.  When both flags are passed, demo wins
    to keep the deterministic path predictable.
    """
    if args.visual_extractor_demo:
        return DemoVisualJobExtractor()
    if args.visual_extractor_provider:
        return build_configured_visual_extractor(
            provider=args.visual_extractor_provider,
            model=args.visual_extractor_model,
        )
    return None


if __name__ == "__main__":
    raise SystemExit(main())
