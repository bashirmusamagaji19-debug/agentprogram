import json

from web_task_agent.cli import main


def test_open_search_demo_produces_verified_job_artifact(tmp_path):
    result = main(
        [
            "--open-search-demo",
            "--query",
            "找北京 Agent 实习，1 个岗位",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert result == 0
    summary = json.loads((tmp_path / "run-summary.json").read_text(encoding="utf-8"))
    assert summary["verified_count"] == 1
    assert (tmp_path / "jobs.jsonl").exists()
