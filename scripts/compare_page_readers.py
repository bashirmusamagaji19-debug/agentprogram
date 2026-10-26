"""Replay identical saved pages through rules and an evidence-backed LLM reader."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from web_task_agent.evidence_reader import EvidenceJobReader  # noqa: E402
from web_task_agent.llm_extractor import build_llm_provider_config  # noqa: E402
from web_task_agent.reading_replay import run_reading_replay  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path,
                        default=ROOT / "portfolio-artifacts/search-fetch-2026-10-17-first")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--provider", choices=["deepseek", "qwen"], default="deepseek")
    parser.add_argument("--model", default=None)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--max-calls", type=int, default=40)
    parser.add_argument("--audit-path", type=Path,
                        default=ROOT / "evaluations/ground-truth/web-reading-audit-2026-10-17.json")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    config = build_llm_provider_config(provider=args.provider, model=args.model)
    output = args.output_dir or ROOT / "portfolio-artifacts" / f"page-reading-{datetime.now():%Y%m%d-%H%M%S}"
    report = asyncio.run(run_reading_replay(
        input_dir=args.input_dir, output_dir=output, reader=EvidenceJobReader(config),
        provider=config.provider, model=config.model, concurrency=args.concurrency, max_calls=args.max_calls,
        audit_path=args.audit_path,
    ))
    print(f"Completed {report['model_calls']} unique page reads. Report: {output.resolve() / 'replay.md'}", flush=True)


if __name__ == "__main__":
    main()
