from web_task_agent import __version__
from web_task_agent.cli import main


def test_package_version_matches_project_version() -> None:
    assert __version__ == "0.1.0"


def test_scaffold_cli_returns_success(capsys) -> None:
    assert main([]) == 0

    captured = capsys.readouterr()
    assert "Web task agent scaffold" in captured.out
