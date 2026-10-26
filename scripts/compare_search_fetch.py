"""Run the bounded two-arm live search experiment without changing UI defaults."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from web_task_agent.deepseek_search import DeepSeekWebClient  # noqa: E402
from web_task_agent.search_comparison import ComparisonCase, run_search_comparison  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keyword", default="大模型 实习")
    parser.add_argument("--cities", nargs="+", default=["北京", "上海"])
    parser.add_argument("--official-specs", default="tencent-campus,xiaomi")
    parser.add_argument("--target-count", type=int, default=5)
    parser.add_argument("--candidate-budget", type=int, default=20)
    parser.add_argument("--max-search-calls", type=int, default=3)
    parser.add_argument("--max-fetch-calls", type=int, default=6)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        parser.error("DEEPSEEK_API_KEY must be configured in the environment or local .env")
    output = args.output_dir or ROOT / "portfolio-artifacts" / f"search-fetch-{datetime.now():%Y%m%d-%H%M%S}"
    cases = [ComparisonCase(
        keyword=args.keyword, location=city, target_count=args.target_count,
        official_specs=[spec.strip() for spec in args.official_specs.split(",") if spec.strip()],
    ) for city in args.cities]
    asyncio.run(run_search_comparison(
        cases=cases, output_dir=output, client_factory=lambda: DeepSeekWebClient(api_key=key),
        candidate_budget=args.candidate_budget, max_search_calls=args.max_search_calls,
        max_fetch_calls=args.max_fetch_calls,
    ))
    print(f"Report: {output.resolve() / 'comparison.md'}", flush=True)


if __name__ == "__main__":
    main()
