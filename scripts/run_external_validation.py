"""Collect ten predeclared live tasks after human review; never label them automatically."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from web_task_agent.model_value_dataset import load_model_value_dataset  # noqa: E402
from web_task_agent.streamlit_runner import UiRunRequest, run_ui_request  # noqa: E402


async def collect(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    tasks = [{"keyword": keyword, "location": city} for city in ("北京", "上海")
             for keyword in ("AI 实习", "大模型 实习", "Agent 实习", "RAG 实习", "算法 实习")]
    manifest = {"created_at": datetime.now(UTC).isoformat(), "scope": "two official sources, rules only",
                "tasks": tasks, "human_delivery_review": "pending", "results": []}
    path = output / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for index, task in enumerate(tasks, 1):
        try:
            result = await run_ui_request(UiRunRequest(
                **task, target_count=1, data_mode="official", engine="hybrid",
                official_specs=["tencent-campus", "xiaomi"], deadline_seconds=45),
                output_root=output / f"task-{index:02}", environ={})
            record = {"task": index, **task, "run_id": result.run_id, "status": result.run_status,
                      "terminal_reason": result.terminal_reason,
                      "jobs": [j.model_dump(mode="json") for j in result.jobs],
                      "source_metrics": result.source_metrics, "source_manifest": result.source_manifest,
                      "human_review": "pending"}
        except Exception as error:  # Preserve failed tasks and continue the declared batch.
            record = {"task": index, **task, "status": "error", "error_type": type(error).__name__,
                      "human_review": "pending"}
        manifest["results"].append(record)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"task {index}/10: {record['status']}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluations/model-value-v4")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    # This must finish before directory creation, HTTP, or source discovery.
    load_model_value_dataset(args.dataset, require_human_review=True)
    asyncio.run(collect(args.output_dir))
