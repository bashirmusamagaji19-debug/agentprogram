import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_acceptance(*args):
    import sys
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(ROOT / "scripts/accept_unified_agent.py"), *map(str, args)],
                          cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)


def test_acceptance_produces_three_saved_jobs_and_refuses_overwrite(tmp_path):
    output = tmp_path / "accepted"
    result = run_acceptance("--mode", "demo", "--keyword", "AI intern", "--location", "Remote",
                            "--target-count", "3", "--planner-strategy", "policy", "--run-id", "run-three",
                            "--output-dir", output)
    assert result.returncode == 0, result.stderr
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["run_id"] == "run-three"
    assert summary["status"] == "succeeded"
    assert summary["valid_jobs"] == summary["saved_jobs"] == 3
    assert summary["source_manifest"]
    assert summary["source_metrics"]["snapshot_integrity"]["value"] == 1.0
    assert summary["source_evidence"]
    assert summary["planner_strategy"] == "policy"
    assert len(list((output / "reports").glob("*.md"))) == 1
    rejected = run_acceptance("--mode", "demo", "--output-dir", output)
    assert rejected.returncode != 0


def test_acceptance_hitl_resumes_in_second_process_and_replays_once(tmp_path):
    output = tmp_path / "hitl"
    started = run_acceptance("--mode", "demo", "--keyword", "AI intern", "--location", "Remote",
                             "--target-count", "3", "--hitl", "--run-id", "run-hitl", "--output-dir", output)
    assert started.returncode == 0, started.stderr
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "awaiting_approval"
    assert summary["saved_jobs"] == 0
    approval_id = summary["approval"]["approval_id"]
    for _ in range(2):
        resumed = run_acceptance("--resume", "approve", "--approval-id", approval_id,
                                 "--run-id", "run-hitl", "--output-dir", output)
        assert resumed.returncode == 0, resumed.stderr
        final = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        assert final["saved_jobs"] == 3
        assert final["status"] == "succeeded"
    assert final["source_manifest"] == summary["source_manifest"]
