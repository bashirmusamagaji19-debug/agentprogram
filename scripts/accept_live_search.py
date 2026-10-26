"""Small live acceptance of the Streamlit search path; no provider calls."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from web_task_agent.search_conditions import job_condition_reasons  # noqa: E402
from web_task_agent.streamlit_runner import UiRunRequest, run_ui_request  # noqa: E402


async def main() -> None:
    output = ROOT / "docs/results/portfolio-acceptance-2026-10-05"
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for city in ("北京", "上海", "北京、上海"):
        print(f"Starting live search: {city}", flush=True)
        started = perf_counter()
        request = UiRunRequest(
            data_mode="official", keyword="大模型", location=city, target_count=2,
            official_specs=["tencent-campus", "xiaomi"], skills=["Python", "RAG"],
        )
        result = await run_ui_request(
            request, output_root=ROOT / "portfolio-artifacts/acceptance-2026-10-05/live", environ={},
        )
        violations = [
            {"url": job.url, "reasons": reasons}
            for job in result.jobs
            if (reasons := job_condition_reasons(request.keyword, city, job))
        ]
        records.append({
            "checked_at": datetime.now(UTC).isoformat(), "run_id": result.run_id,
            "keyword": request.keyword, "location": city, "target_count": 2,
            "elapsed_seconds": round(perf_counter() - started, 2),
            "metrics": result.metrics.model_dump(mode="json"),
            "jobs": [job.model_dump(mode="json") for job in result.jobs],
            "source_diagnostics": result.source_diagnostics, "diagnostics": result.diagnostics,
            "condition_violations": violations,
            "positive_acceptance_passed": not violations and len(result.jobs) == request.target_count,
            "raw_json": result.artifacts["json"].relative_to(ROOT).as_posix(),
        })
        (output / "live-search.json").write_text(
            json.dumps({"scope": "two official sources, bounded sampling, no LLM", "runs": records},
                       ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"Completed {city}: {len(result.jobs)} valid jobs, {len(violations)} condition violations", flush=True)
        assert not violations, violations
        assert len(result.jobs) == request.target_count, (
            f"{city}: expected {request.target_count} qualifying samples, got {len(result.jobs)}; "
            "source failure or empty results are recorded but cannot prove positive search acceptance"
        )


if __name__ == "__main__":
    asyncio.run(main())
