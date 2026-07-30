from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from web_task_agent.agent_release_check import run_release_checks


def test_release_check_runs_ci_equivalent_named_stages(tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    result = run_release_checks(repo_root=tmp_path, runner=runner)

    assert result.passed is True
    assert [stage.name for stage in result.stages] == [
        "focused-ruff",
        "pytest-coverage",
        "wheel-build",
        "doctor",
        "strict-hitl",
        "git-diff-check",
    ]
    assert all(stage.passed for stage in result.stages)
    assert len(calls) == 6
    strict_call = calls[4]
    assert strict_call[1]["env"]["LANGGRAPH_STRICT_MSGPACK"] == "true"


def test_release_check_reports_failed_stage_without_hiding_later_checks(tmp_path):
    calls = 0

    def runner(_command, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            returncode=1 if calls == 2 else 0,
            stdout="",
            stderr="coverage failed" if calls == 2 else "",
        )

    result = run_release_checks(repo_root=Path(tmp_path), runner=runner)

    assert result.passed is False
    assert result.stages[1].name == "pytest-coverage"
    assert result.stages[1].passed is False
    assert "coverage failed" in result.stages[1].output
    assert len(result.stages) == 6
