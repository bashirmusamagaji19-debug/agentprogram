from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from web_task_agent import __version__

# Load .env before anything else reads os.environ
# __file__ = .../Agent/src/web_task_agent/cli.py → parents[2] = .../Agent
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
from web_task_agent.action_plan import ActionPlanWriter
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
        try:
            result = await run_llm_extractor_comparison(args)
        except LlmExtractorConfigurationError as exc:
            print(f"LLM extractor is not configured: {exc}")
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

    if not args.keyword and not args.seed_url:
        print("--keyword is required unless --evaluate is used.")
        return 2

    browser = build_browser(demo=args.demo)
    try:
        llm_field_extractor = build_cli_llm_field_extractor(args)
        llm_matcher = build_cli_llm_matcher(args)
    except LlmExtractorConfigurationError as exc:
        print(f"LLM extractor is not configured: {exc}")
        return 2
    workflow = build_workflow(
        browser=browser,
        db_path=args.db_path,
        report_dir=args.report_dir,
        llm_field_extractor=llm_field_extractor,
        llm_matcher=llm_matcher,
    )
    if llm_matcher is not None:
        mode = f"llm-match-{args.llm_match_provider or 'demo'}"
        print(f"LLM match enabled: {mode}")
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
    if args.llm_extractor_provider:
        model = getattr(llm_field_extractor, "model", args.llm_extractor_model or "")
        print(f"LLM extractor provider: {args.llm_extractor_provider}")
        state.metadata["extractor_mode"] = "llm-provider"
        state.metadata["llm_provider"] = args.llm_extractor_provider
        state.metadata["llm_model"] = model
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
    extractors = {
        "baseline": baseline.model_dump(mode="json"),
        "llm_demo": llm_demo.model_dump(mode="json"),
    }
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


if __name__ == "__main__":
    raise SystemExit(main())
