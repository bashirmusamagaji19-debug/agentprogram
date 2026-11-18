"""Explicit live acceptance using synthetic profiles and production link gates."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from web_task_agent.source_catalog import LIMITED_SOURCES, VERIFIED_SOURCES  # noqa: E402
from web_task_agent.streamlit_runner import UiRunRequest, run_ui_request  # noqa: E402

AUDIT_SOURCES = {**VERIFIED_SOURCES, **LIMITED_SOURCES}


async def audit(args):
    rows = []
    for name in args.sources:
        keyword = AUDIT_SOURCES[name].get("validation_keyword", "AI Agent 实习")
        result = await run_ui_request(
            UiRunRequest(data_mode="official", official_specs=[name], engine="hybrid",
                         keyword=keyword, location="全国", target_count=2, deadline_seconds=210),
            output_root=args.output / "runs", environ={})
        jobs = [{"title": job.title, "company": job.company, "location": job.location,
                 "url": job.url, "receipt": job.link_verification} for job in result.jobs]
        row = {"source": name, "keyword": keyword, "run_id": result.run_id, "status": result.run_status,
               "terminal_reason": result.terminal_reason, "progress": result.progress,
               "source_diagnostics": result.source_diagnostics, "diagnostics": result.diagnostics,
               "samples": jobs, "accepted": len(jobs) >= 1 and all(
                   j["receipt"].get("status") == "verified" for j in jobs)}
        rows.append(row)
        report = {"checked_at": datetime.now(UTC).isoformat(), "platform": platform.platform(),
                  "location": "全国", "target_per_source": 2, "minimum_verified_sample": 1,
                  "scope": "Sample acceptance, not a claim that every job/city or future request succeeds.",
                  "sources": rows}
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"source": name, "accepted": row["accepted"], "progress": result.progress}), flush=True)
    return all(row["accepted"] for row in rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", nargs="+", choices=list(AUDIT_SOURCES),
                        default=os.environ.get("AUDIT_SOURCE_SPECS", "").split() or list(VERIFIED_SOURCES))
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "live-source-audit")
    arguments = parser.parse_args()
    if any(source not in AUDIT_SOURCES for source in arguments.sources):
        parser.error("AUDIT_SOURCE_SPECS contains an unregistered source")
    raise SystemExit(0 if asyncio.run(audit(arguments)) else 1)
