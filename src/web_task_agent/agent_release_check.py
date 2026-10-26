from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


@dataclass(frozen=True)
class ReleaseCheckStage:
    name: str
    passed: bool
    output: str


@dataclass(frozen=True)
class ReleaseCheckResult:
    stages: tuple[ReleaseCheckStage, ...]

    @property
    def passed(self) -> bool:
        return all(stage.passed for stage in self.stages)


Runner = Callable[..., object]


def run_release_checks(
    *,
    repo_root: str | Path,
    runner: Runner = subprocess.run,
) -> ReleaseCheckResult:
    root = Path(repo_root).resolve()
    python = sys.executable
    cli_code = "from web_task_agent.cli import main; raise SystemExit(main(%r))"
    ruff_targets = ["src", "tests"]

    with TemporaryDirectory(prefix="web-task-agent-release-") as temp_dir:
        temp = Path(temp_dir)
        strict_env = os.environ.copy()
        strict_env["LANGGRAPH_STRICT_MSGPACK"] = "true"
        stages: list[tuple[str, Sequence[str], dict[str, str] | None]] = [
            (
                "focused-ruff",
                [
                    python,
                    "-m",
                    "ruff",
                    "check",
                    *ruff_targets,
                ],
                None,
            ),
            (
                "pytest-coverage",
                [
                    python,
                    "-m",
                    "pytest",
                    "-p",
                    "no:cacheprovider",
                    "--cov=web_task_agent",
                    "--cov-report=term-missing",
                    "--cov-fail-under=70",
                ],
                None,
            ),
            (
                "wheel-build",
                [
                    python,
                    "-m",
                    "pip",
                    "wheel",
                    ".",
                    "--no-deps",
                    "--wheel-dir",
                    str(temp / "wheel"),
                ],
                None,
            ),
            (
                "doctor",
                [python, "-c", cli_code % ["--doctor"]],
                None,
            ),
            (
                "strict-hitl",
                [
                    python,
                    "-c",
                    cli_code
                    % [
                        "--hitl-benchmark",
                        "--hitl-benchmark-output-dir",
                        str(temp / "hitl"),
                    ],
                ],
                strict_env,
            ),
            ("git-diff-check", ["git", "diff", "--check"], None),
        ]

        results = []
        for name, command, environment in stages:
            completed = runner(
                command,
                cwd=root,
                env={**(environment if environment is not None else os.environ), "PYTHONIOENCODING": "utf-8"},
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            stdout = str(getattr(completed, "stdout", "") or "")
            stderr = str(getattr(completed, "stderr", "") or "")
            output = "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
            results.append(
                ReleaseCheckStage(
                    name=name,
                    passed=int(getattr(completed, "returncode", 1)) == 0,
                    output=output,
                )
            )
    return ReleaseCheckResult(stages=tuple(results))
