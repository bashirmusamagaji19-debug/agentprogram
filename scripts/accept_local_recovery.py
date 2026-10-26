"""Independent-process acceptance for local UI artifacts and durable HITL."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from web_task_agent.artifact_store import FileArtifactStore  # noqa: E402
from web_task_agent.run_store import FileRunStore  # noqa: E402
from web_task_agent.storage import JobRepository  # noqa: E402
from web_task_agent.streamlit_runner import (  # noqa: E402
    UiRunRequest,
    load_persisted_ui_result,
    run_ui_request,
)

EVIDENCE = ROOT / "docs/results/portfolio-acceptance-2026-10-05"
CLI = "import sys; from web_task_agent.cli import main; raise SystemExit(main(sys.argv[1:]))"


def run_process(args: list[str], *, code: str | None = None) -> subprocess.CompletedProcess:
    command = [sys.executable, "-c", code or CLI, *args] if code else [sys.executable, *args]
    return subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


async def create_ui(root: Path) -> None:
    result = await run_ui_request(
        UiRunRequest(keyword="AI intern", target_count=2), output_root=root / "output", environ={},
        run_store=FileRunStore(root / "runs"), artifact_store=FileArtifactStore(root / "artifacts"),
        owner_id="acceptance-owner",
    )
    (root / "created.json").write_text(json.dumps({
        "creator_pid": os.getpid(), "run_id": result.run_id,
        "sha256": {key: record.sha256 for key, record in result.artifact_records.items()},
    }), encoding="utf-8")


def restore_ui(root: Path) -> None:
    created = json.loads((root / "created.json").read_text(encoding="utf-8"))
    run_store, artifact_store = FileRunStore(root / "runs"), FileArtifactStore(root / "artifacts")
    restored = load_persisted_ui_result(run_store, artifact_store, "acceptance-owner", created["run_id"])
    assert restored is not None and len(restored.jobs) == 2
    assert created["creator_pid"] != os.getpid()
    checked = {}
    for key, expected in created["sha256"].items():
        record, content = artifact_store.get_artifact("acceptance-owner", created["run_id"], key)
        checked[key] = hashlib.sha256(content).hexdigest() == expected == record.sha256
        assert checked[key]
    assert set(checked) == {"json", "report", "dashboard", "action_plan"}
    assert load_persisted_ui_result(run_store, artifact_store, "other-owner", created["run_id"]) is None
    record = run_store.get_run("acceptance-owner", created["run_id"])
    assert record is not None and record.status == "succeeded"
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "local-persistence.json").write_text(json.dumps({
        "adapter": "FileRunStore/FileArtifactStore", "run_id": created["run_id"],
        "creator_pid": created["creator_pid"], "restorer_pid": os.getpid(),
        "status": record.status, "valid_jobs": len(restored.jobs), "artifact_hash_matches": checked,
        "other_owner_denied": True, "sha256": created["sha256"],
    }, indent=2), encoding="utf-8")


def accept_hitl(root: Path) -> None:
    cases = []
    for outcome in ("approve", "reject"):
        case_root = root / outcome
        case_root.mkdir()
        thread_id = f"acceptance-{outcome}-{uuid4().hex[:8]}"
        common = [
            "--demo", "--hybrid-agent", "--hitl", "--thread-id", thread_id,
            "--checkpoint-db", str(case_root / "checkpoints.sqlite"),
            "--db-path", str(case_root / "jobs.sqlite"),
            "--report-dir", str(case_root / "reports"), "--dashboard-dir", str(case_root / "dashboards"),
        ]
        paused_path, resumed_path = case_root / "paused.json", case_root / "resumed.json"
        paused = run_process([
            *common, "--keyword", "AI intern", "--target-count", "1", "--skill", "Python",
            "--json-output", str(paused_path),
        ], code=CLI)
        assert paused.returncode == 0, "HITL start failed"
        state = json.loads(paused_path.read_text(encoding="utf-8"))
        assert state["hitl_status"] == "awaiting_approval"
        approval_id = state["pending_approval"]["approval_id"]
        repository = JobRepository(case_root / "jobs.sqlite")
        assert not repository.list_jobs()
        resumed = run_process([
            *common, "--resume-approval", outcome, "--approval-id", approval_id,
            "--json-output", str(resumed_path),
        ], code=CLI)
        assert resumed.returncode == 0, "HITL resume failed"
        final = json.loads(resumed_path.read_text(encoding="utf-8"))
        saved = len(repository.list_jobs())
        assert saved == (1 if outcome == "approve" else 0)
        assert final["terminal_reason"] == ("target_reached" if outcome == "approve" else "human_denied")
        replay = run_process([
            *common, "--resume-approval", outcome, "--approval-id", approval_id,
            "--json-output", str(case_root / "replayed.json"),
        ], code=CLI)
        assert replay.returncode == 2 and len(repository.list_jobs()) == saved
        cases.append({
            "outcome": outcome, "thread_id": thread_id, "paused_before_save": True,
            "start_exit": paused.returncode, "resume_exit": resumed.returncode,
            "saved_effects": saved, "terminal_reason": final["terminal_reason"],
            "duplicate_resume_exit": replay.returncode, "duplicate_effects": 0,
            "trace": final["trace"],
        })
        print(f"HITL {outcome}: saved={saved}, duplicate resume blocked", flush=True)
    (EVIDENCE / "cross-process-hitl.json").write_text(
        json.dumps({"independent_cli_processes": True, "cases": cases}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["all", "create", "restore"], default="all")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.phase == "create":
        asyncio.run(create_ui(args.root))
    elif args.phase == "restore":
        restore_ui(args.root)
    else:
        root = ROOT / "portfolio-artifacts/acceptance-2026-10-05/recovery" / uuid4().hex[:8]
        root.mkdir(parents=True)
        for phase in ("create", "restore"):
            process = run_process([str(Path(__file__).resolve()), "--phase", phase, "--root", str(root)])
            assert process.returncode == 0, f"UI {phase} acceptance failed: {process.stderr[-1000:]}"
        print("UI cross-process restore: four artifact hashes match; owner isolation passed", flush=True)
        accept_hitl(root)


if __name__ == "__main__":
    main()
