from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from web_task_agent.artifact_store import FileArtifactStore  # noqa: E402
from web_task_agent.run_store import FileRunStore  # noqa: E402
from web_task_agent.streamlit_runner import (  # noqa: E402
    UiRunRequest,
    load_persisted_ui_result,
    run_ui_request,
)


def build_stores(root: Path) -> tuple[FileRunStore, FileArtifactStore]:
    return FileRunStore(root / "runs"), FileArtifactStore(root / "artifacts")


async def create_run(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.root)
    run_store, artifact_store = build_stores(root)
    result = await run_ui_request(
        UiRunRequest(
            keyword="AI intern",
            target_count=2,
            skills=["Python", "LangGraph"],
        ),
        output_root=root / "output",
        environ={},
        run_store=run_store,
        artifact_store=artifact_store,
        owner_id=args.owner,
    )
    record = run_store.get_run(args.owner, result.run_id)
    artifacts = {
        item.artifact_key: item.sha256
        for item in artifact_store.list_artifacts(args.owner, result.run_id)
    }
    return {
        "run_id": result.run_id,
        "owner_id": args.owner,
        "status": record.status if record is not None else None,
        "artifact_sha256": artifacts,
    }


def verify_run(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.root)
    run_store, artifact_store = build_stores(root)
    record = run_store.get_run(args.owner, args.run_id)
    if record is None:
        raise SystemExit("run record not found")
    result = load_persisted_ui_result(
        run_store,
        artifact_store,
        args.owner,
        args.run_id,
    )
    if result is None:
        raise SystemExit("persisted result not found")
    hash_matches: dict[str, bool] = {}
    for artifact_key, metadata in result.artifact_records.items():
        _, content = artifact_store.get_artifact(args.owner, args.run_id, artifact_key)
        hash_matches[artifact_key] = hashlib.sha256(content).hexdigest() == metadata.sha256
    return {
        "run_id": result.run_id,
        "owner_id": record.owner_id,
        "status": record.status,
        "valid_jobs": result.metrics.valid_jobs,
        "artifact_count": len(result.artifact_records),
        "hash_matches": hash_matches,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create", "verify"])
    parser.add_argument("--root", required=True)
    parser.add_argument("--owner", default="owner-a")
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()
    if args.command == "verify" and not args.run_id:
        parser.error("--run-id is required for verify")
    return args


def main() -> None:
    args = parse_args()
    payload = asyncio.run(create_run(args)) if args.command == "create" else verify_run(args)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
