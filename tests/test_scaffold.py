from pathlib import Path

from web_task_agent import __version__
from web_task_agent.cli import main


def test_package_version_matches_project_version() -> None:
    assert __version__ == "0.1.0"


def test_cli_demo_mode_writes_report(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--keyword",
                "AI intern",
                "--location",
                "Remote",
                "--target-count",
                "2",
                "--demo",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Report written to:" in captured.out
    assert "Valid jobs:" in captured.out
    reports = list(Path("reports").glob("*.md"))
    assert len(reports) == 1
    assert "AI 实习岗位搜索报告" in reports[0].read_text(encoding="utf-8")


def test_cli_non_demo_mode_exits_with_clear_message(capsys) -> None:
    assert main(["--keyword", "AI intern"]) == 2

    captured = capsys.readouterr()
    assert "not implemented yet" in captured.out
    assert "--demo" in captured.out
