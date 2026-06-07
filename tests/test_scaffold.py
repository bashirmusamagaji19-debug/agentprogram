from web_task_agent import __version__
from web_task_agent.cli import main


def test_package_version_matches_project_version() -> None:
    assert __version__ == "0.1.0"


def test_scaffold_cli_returns_success(capsys) -> None:
    assert main([]) == 0

    captured = capsys.readouterr()
    assert "Web task agent scaffold" in captured.out


def test_scaffold_cli_accepts_documented_arguments(capsys) -> None:
    assert (
        main(
            [
                "--keyword",
                "AI engineering intern",
                "--location",
                "Remote",
                "--target-count",
                "3",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "not implemented yet" in captured.out
    assert "AI engineering intern" in captured.out
    assert "Remote" in captured.out
    assert "3" in captured.out
