"""Capture, verify and replay a source archive through the shared Agent service."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from web_task_agent.agent_acceptance import build_parser as unified_parser
from web_task_agent.agent_acceptance import run_acceptance, write_json
from web_task_agent.agent_components import AgentServices
from web_task_agent.agent_service import AgentRunOptions, start_agent_run
from web_task_agent.evidence_reader import PageReading, assess_reading
from web_task_agent.extractor import PageExtractor
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import JobPosting, UserProfile
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.source_archive import SourceArchive
from web_task_agent.source_replay import ArchiveBrowser, ConditionsChangedError, RecordedEvidenceReader
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["capture", "replay", "verify"], required=True)
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--specs", default="tencent-campus,xiaomi,xpeng")
    parser.add_argument("--keyword")
    parser.add_argument("--location")
    parser.add_argument("--target-count", type=int)
    parser.add_argument("--replay-mode", choices=["content", "attempts"], default="content")
    parser.add_argument("--reader", choices=["rules", "recorded"], default="rules")
    parser.add_argument("--reevaluate-conditions", action="store_true")
    parser.add_argument("--deadline-seconds", type=float, default=180)
    parser.add_argument("--model-records", type=Path)
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--model-parameters", type=Path)
    return parser


def find_archive(path: Path) -> Path:
    path = path.resolve()
    if path.is_file() and path.name == "manifest.json":
        return path.parent
    if (path / "manifest.json").is_file():
        return path
    matches = sorted(path.glob("source-archive/*/manifest.json"))
    if len(matches) != 1:
        raise ValueError("input must identify exactly one source archive")
    return matches[0].parent


def normalized_jobs(jobs):
    return sorted((job["url"], job["title"], job["company"], job["location"]) for job in jobs)


class RecordedPageExtractor:
    def __init__(self, reader, *, keyword, location):
        self.reader, self.keyword, self.location = reader, keyword, location

    def extract(self, page):
        record = self.reader.read(page)
        reading = PageReading.model_validate(record.get("reading", record))
        result = assess_reading(reading, page, keyword=self.keyword, location=self.location)
        if result["status"] != "accepted" or len(result["jobs"]) != 1:
            raise ValueError("recorded_reading_not_accepted")
        return JobPosting.model_validate(result["jobs"][0])


async def run_source_acceptance(args):
    if args.mode == "capture":
        if args.output_dir is None or args.input_dir is not None or args.reader != "rules":
            raise ValueError("capture requires a fresh output directory and the rules reader")
        forwarded = unified_parser().parse_args([
            "--mode", "official", "--keyword", args.keyword or "AI Agent 实习",
            "--location", args.location or "北京、上海", "--target-count", str(args.target_count or 3),
            "--specs", args.specs, "--planner-strategy", "policy",
            "--deadline-seconds", str(args.deadline_seconds), "--output-dir", str(args.output_dir),
        ])
        return await run_acceptance(forwarded)
    if args.input_dir is None:
        raise ValueError("verify/replay requires --input-dir")
    root = find_archive(args.input_dir)
    archive = SourceArchive(root)
    integrity = archive.verify()
    if args.mode == "verify":
        print(json.dumps({"status": "verified", **integrity}))
        return 0
    if args.output_dir is None:
        raise ValueError("replay requires --output-dir")
    manifest = archive.read_manifest()
    conditions = dict(manifest.conditions)
    for key, value in (("keyword", args.keyword), ("location", args.location), ("target_count", args.target_count)):
        if value is not None:
            conditions[key] = value
    matched = conditions == manifest.conditions
    if not matched and not args.reevaluate_conditions:
        raise ConditionsChangedError(manifest.conditions, conditions)
    recorded_reader = None
    if args.reader == "recorded":
        if not all((args.model_records, args.provider, args.model, args.prompt_file)):
            raise ValueError("recorded replay requires exact model records, provider, model and prompt file")
        recorded_reader = RecordedEvidenceReader(
            args.model_records, provider=args.provider, model=args.model,
            prompt=args.prompt_file.read_text(encoding="utf-8"),
            parameters=json.loads(args.model_parameters.read_text(encoding="utf-8")) if args.model_parameters else None,
        )
    browser = ArchiveBrowser(root, mode=args.replay_mode, conditions=conditions,
                             conditions_mismatch="use_archived" if args.reevaluate_conditions else "error",
                             recorded_reader=recorded_reader)
    run_dir = args.output_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    repository = JobRepository(run_dir / "agent.db")
    repository.initialize()
    seeds = list(dict.fromkeys(c.requested_url for c in archive.read_candidates()
                               if c.query_context.get("type") != "search_page"))
    user = UserProfile(keyword=str(conditions["keyword"]), location=str(conditions["location"]),
                       target_count=int(conditions["target_count"]), seed_urls=seeds,
                       skills=["Python", "LangGraph", "LLM"])
    extractor = (RecordedPageExtractor(recorded_reader, keyword=user.keyword, location=user.location)
                 if recorded_reader else PageExtractor())
    workflow = WebTaskWorkflow(browser=browser, extractor=extractor, verifier=JobVerifier(), matcher=JobMatcher(),
                               repository=repository, reporter=MarkdownReporter(run_dir / "reports"),
                               enforce_search_conditions=True)
    run_id = f"replay-{uuid4().hex[:8]}"
    result = await start_agent_run(user, run_id=run_id, owner_id="source-acceptance",
                                   services=AgentServices(workflow=workflow, run_dir=run_dir),
                                   options=AgentRunOptions(engine="hybrid", deadline_seconds=args.deadline_seconds))
    comparison = None
    for parent in (root.parent, root.parent.parent):
        previous = parent / "result.json"
        if previous.is_file():
            original = json.loads(previous.read_text(encoding="utf-8"))
            comparison = normalized_jobs(original.get("jobs", [])) == normalized_jobs(
                [job.model_dump(mode="json") for job in result.view.jobs])
            break
    summary = {"run_id": run_id, "status": result.status, "terminal_reason": result.agent_state.terminal_reason,
               "valid_jobs": len(result.view.jobs), "saved_jobs": len(repository.list_jobs()),
               "reader": args.reader, "replay_mode": args.replay_mode, "network_calls": 0,
               "live_model_calls": 0, "conditions_match": matched, "conditions": conditions,
               "source_manifest": str(root / "manifest.json"), "source_captured_at": manifest.created_at.isoformat(),
               "integrity": integrity, "normalized_results_match": comparison}
    write_json(run_dir / "summary.json", summary)
    write_json(run_dir / "result.json", result.view.model_dump(mode="json"))
    (run_dir / "report.md").write_text(
        "# 来源归档回放\n\n" + f"模式：{args.replay_mode}；Reader：{args.reader}。\n\n"
        + "回放只读取已归档证据；原采集事实不证明岗位目前仍开放。\n\n"
        + f"结果：{result.status}，交付 {len(result.view.jobs)}，保存 {len(repository.list_jobs())}。\n\n"
        + f"原采集会话时间：{manifest.created_at.isoformat()}。\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("status", "valid_jobs", "saved_jobs", "conditions_match")}))
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.mode == "verify":
            if args.input_dir is None:
                raise ValueError("verify requires --input-dir")
            result = SourceArchive(find_archive(args.input_dir)).verify()
            print(json.dumps({"status": "verified", **result}))
            return 0
        return asyncio.run(run_source_acceptance(args))
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(2, f"Source acceptance failed: {type(error).__name__}\n")


if __name__ == "__main__":
    raise SystemExit(main())
