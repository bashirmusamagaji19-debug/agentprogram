from pathlib import Path

from web_task_agent import __version__
from web_task_agent.browser import BrowserConfigurationError
from web_task_agent.cli import main
from web_task_agent.models import BrowserPage


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


def test_cli_demo_mode_can_run_with_langgraph(tmp_path, monkeypatch, capsys) -> None:
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
                "--langgraph",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "LangGraph workflow: enabled" in captured.out
    assert "Report written to:" in captured.out
    assert list(Path("reports").glob("*.md"))


def test_cli_demo_mode_writes_dashboard(tmp_path, monkeypatch, capsys) -> None:
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
                "--skill",
                "Python",
                "--demo",
                "--dashboard",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Dashboard written to:" in captured.out
    dashboards = list(Path("dashboards").glob("*.html"))
    assert len(dashboards) == 1
    content = dashboards[0].read_text(encoding="utf-8")
    assert "Web Task Agent Dashboard" in content
    assert "匹配分数" in content


def test_cli_non_demo_mode_exits_with_clear_message(monkeypatch, capsys) -> None:
    class FailingBrowser:
        async def search(self, query: str, target_count: int) -> list[object]:
            raise BrowserConfigurationError("missing local browser")

        async def open_url(self, url: str) -> object:
            raise AssertionError("open_url should not be called")

    monkeypatch.setattr(
        "web_task_agent.cli.build_browser",
        lambda *, demo: FailingBrowser(),
    )

    assert main(["--keyword", "AI intern"]) == 2

    captured = capsys.readouterr()
    assert "Real browser-use mode is not configured" in captured.out
    assert "Use --demo" in captured.out


def test_cli_evaluate_mode_writes_report(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["--evaluate", "--evaluation-count", "3"]) == 0

    captured = capsys.readouterr()
    assert "Evaluation report written to:" in captured.out
    report = Path("evaluations") / "evaluation-report.md"
    assert report.exists()
    content = report.read_text(encoding="utf-8")
    assert "任务成功率" in content
    assert "任务总数: 3" in content


def test_cli_real_smoke_evaluate_uses_real_browser_factory(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    opened_queries: list[str] = []

    class SmokeBrowser:
        async def search(self, query: str, target_count: int) -> list[BrowserPage]:
            opened_queries.append(query)
            return []

        async def open_url(self, url: str) -> BrowserPage:
            raise AssertionError("open_url should not be called")

    monkeypatch.setattr(
        "web_task_agent.cli.BrowserUseClient",
        lambda: SmokeBrowser(),
    )

    assert main(["--evaluate", "--real-smoke"]) == 0

    captured = capsys.readouterr()
    assert "Evaluation report written to:" in captured.out
    assert "Completed tasks: 0/3" in captured.out
    assert len(opened_queries) == 9
    assert any(query.startswith("AI intern") for query in opened_queries)
    assert any(query.startswith("LLM agent intern") for query in opened_queries)
    assert any(query.startswith("AI engineering intern") for query in opened_queries)
    report = (tmp_path / "evaluations" / "evaluation-report.md").read_text(
        encoding="utf-8"
    )
    assert "no_pages" in report


def test_cli_fixture_sites_evaluate_writes_success_report(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["--evaluate", "--fixture-sites"]) == 0

    captured = capsys.readouterr()
    assert "Completed tasks: 2/2" in captured.out
    report = (tmp_path / "evaluations" / "evaluation-report.md").read_text(
        encoding="utf-8"
    )
    assert "AI Agent Engineering Intern" in report
    assert "LLM Application Intern" in report
    assert "| - | 0 |" in report


def test_cli_evaluate_dashboard_writes_evaluation_html(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["--evaluate", "--fixture-sites", "--dashboard"]) == 0

    captured = capsys.readouterr()
    assert "Evaluation dashboard written to:" in captured.out
    dashboards = list((tmp_path / "dashboards").glob("evaluation-*.html"))
    assert len(dashboards) == 1
    html = dashboards[0].read_text(encoding="utf-8")
    assert "Evaluation Summary" in html
    assert "任务成功率" in html
