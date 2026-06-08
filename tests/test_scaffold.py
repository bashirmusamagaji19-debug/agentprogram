import json
from pathlib import Path

import pytest

from web_task_agent import __version__
from web_task_agent.browser import BrowserConfigurationError
from web_task_agent.cli import load_resume_text, main, write_json_output
from web_task_agent.models import BrowserPage, UserProfile, WorkflowState
from web_task_agent.site_fixtures import PUBLIC_JOB_FIXTURE_PAGES


def test_package_version_matches_project_version() -> None:
    assert __version__ == "0.1.0"


def test_cli_prints_version(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "web-task-agent 0.1.0" in captured.out


def test_cli_doctor_prints_environment_checks(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["--doctor"]) == 0

    captured = capsys.readouterr()
    assert "Environment doctor" in captured.out
    assert "python:" in captured.out
    assert "langgraph: ok" in captured.out
    assert "browser_use:" in captured.out
    assert "reports: writable" in captured.out
    assert "dashboards: writable" in captured.out


def test_cli_lists_public_fixture_urls(capsys) -> None:
    assert main(["--list-fixture-urls"]) == 0

    captured = capsys.readouterr()
    assert "Fixture job URLs" in captured.out
    assert "https://boards.greenhouse.io/example/jobs/ai-agent-intern" in captured.out
    assert "https://jobs.lever.co/example/llm-application-intern" in captured.out


def test_cli_prints_demo_script(capsys) -> None:
    assert main(["--print-demo-script"]) == 0

    captured = capsys.readouterr()
    assert "Demo script" in captured.out
    assert "--doctor" in captured.out
    assert "--list-fixture-urls" in captured.out
    assert "--seed-url" in captured.out
    assert "--action-plan" in captured.out
    assert "--llm-extractor-demo" in captured.out
    assert "--evaluate --fixture-sites" in captured.out
    assert "7. " in captured.out


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


def test_cli_demo_mode_can_use_llm_extractor_demo(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--keyword",
                "AI intern",
                "--target-count",
                "1",
                "--demo",
                "--llm-extractor-demo",
                "--json-output",
                "outputs/llm-extractor-demo.json",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "LLM extractor demo: enabled" in captured.out
    payload = json.loads(
        (tmp_path / "outputs" / "llm-extractor-demo.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["metadata"]["extractor_mode"] == "llm-demo"


def test_cli_llm_extractor_demo_recovers_unstructured_seed_page(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--seed-url",
                "https://example.com/jobs/unstructured-ai-agent-intern",
                "--target-count",
                "1",
                "--demo",
                "--llm-extractor-demo",
                "--json-output",
                "outputs/unstructured-llm.json",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "LLM extractor demo: enabled" in captured.out
    assert "Valid jobs: 1" in captured.out
    payload = json.loads(
        (tmp_path / "outputs" / "unstructured-llm.json").read_text(encoding="utf-8")
    )
    assert payload["jobs"][0]["title"] == "AI Agent Intern"
    assert payload["jobs"][0]["company"] == "Example Robotics"


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


def test_cli_can_export_langgraph_markdown(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["--export-graph"]) == 0

    captured = capsys.readouterr()
    assert "Graph written to:" in captured.out
    graph_path = tmp_path / "docs" / "agent-workflow-graph.md"
    assert graph_path.exists()
    assert "```mermaid" in graph_path.read_text(encoding="utf-8")


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


def test_cli_demo_mode_writes_action_plan(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--keyword",
                "AI intern",
                "--target-count",
                "2",
                "--skill",
                "Python",
                "--demo",
                "--action-plan",
                "--json-output",
                "outputs/result.json",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Action plan written to:" in captured.out
    assert "JSON output written to:" in captured.out
    plans = list(Path("action-plans").glob("*.md"))
    assert len(plans) == 1
    reports = list(Path("reports").glob("*.md"))
    assert len(reports) == 1
    content = plans[0].read_text(encoding="utf-8")
    assert "# AI 实习行动计划" in content
    assert "技能补强顺序" in content
    report = reports[0].read_text(encoding="utf-8")
    assert "## 相关产物" in report
    assert f"- 行动计划: {plans[0].as_posix()}" in report
    payload = json.loads((tmp_path / "outputs" / "result.json").read_text(encoding="utf-8"))
    assert payload["metadata"]["action_plan_path"] == plans[0].as_posix()


def test_cli_demo_dashboard_includes_search_query_trace(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--keyword",
                "AI intern",
                "--target-count",
                "1",
                "--demo",
                "--dashboard",
            ]
        )
        == 0
    )

    capsys.readouterr()
    html = next(Path("dashboards").glob("*.html")).read_text(encoding="utf-8")
    assert "Input Trace" in html
    assert "Search query mode" in html
    assert "AI intern Remote" in html


def test_cli_demo_mode_writes_json_output(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--keyword",
                "AI intern",
                "--target-count",
                "2",
                "--skill",
                "Python",
                "--resume-text",
                "Built LangGraph browser agents with LLM evaluation loops.",
                "--demo",
                "--json-output",
                "outputs/result.json",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "JSON output written to:" in captured.out
    payload = json.loads((tmp_path / "outputs" / "result.json").read_text(encoding="utf-8"))
    assert payload["user"]["keyword"] == "AI intern"
    assert payload["metrics"]["valid_jobs"] == 2
    assert len(payload["jobs"]) == 2
    assert payload["matches"][0]["score"] == 1.0
    assert payload["report_path"].endswith(".md")


def test_cli_demo_mode_accepts_seed_url_without_keyword(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--seed-url",
                "https://example.com/jobs/ai-engineering-intern",
                "--target-count",
                "1",
                "--demo",
                "--json-output",
                "outputs/seed-result.json",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Report written to:" in captured.out
    assert "Valid jobs: 1" in captured.out
    payload = json.loads(
        (tmp_path / "outputs" / "seed-result.json").read_text(encoding="utf-8")
    )
    assert payload["user"]["keyword"] == "seed URLs"
    assert payload["user"]["seed_urls"] == [
        "https://example.com/jobs/ai-engineering-intern"
    ]
    assert payload["candidate_urls"] == [
        "https://example.com/jobs/ai-engineering-intern"
    ]


def test_cli_demo_mode_uses_resume_file_for_matching(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    resume_path = tmp_path / "resume.md"
    resume_path.write_text(
        "Built LangGraph browser agents with LLM evaluation loops.",
        encoding="utf-8",
    )

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
                "--resume-file",
                str(resume_path),
                "--demo",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Report written to:" in captured.out
    report = next(Path("reports").glob("*.md")).read_text(encoding="utf-8")
    assert "- 匹配分数: 1.00" in report


def test_cli_demo_mode_combines_resume_text_and_resume_file(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    resume_path = tmp_path / "resume.md"
    resume_path.write_text("Shipped LangGraph workflows.", encoding="utf-8")

    assert (
        main(
            [
                "--keyword",
                "AI intern",
                "--target-count",
                "2",
                "--skill",
                "Python",
                "--resume-text",
                "Worked on LLM application evaluation.",
                "--resume-file",
                str(resume_path),
                "--demo",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Report written to:" in captured.out
    report = next(Path("reports").glob("*.md")).read_text(encoding="utf-8")
    assert "- 匹配分数: 1.00" in report


def test_cli_missing_resume_file_exits_with_clear_message(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--keyword",
                "AI intern",
                "--resume-file",
                str(tmp_path / "missing.md"),
                "--demo",
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert "Resume file not found:" in captured.out


def test_load_resume_text_combines_inline_text_and_files(tmp_path) -> None:
    first_file = tmp_path / "first.md"
    second_file = tmp_path / "second.md"
    first_file.write_text("LangGraph workflow", encoding="utf-8")
    second_file.write_text("LLM evaluation", encoding="utf-8")

    resume_text = load_resume_text(
        ["  Python browser agent  ", ""],
        [str(first_file), str(second_file)],
    )

    assert resume_text == (
        "Python browser agent\n\nLangGraph workflow\n\nLLM evaluation"
    )


def test_write_json_output_creates_parent_directory(tmp_path) -> None:
    state = WorkflowState(user=UserProfile(keyword="AI intern"))

    path = write_json_output(state, str(tmp_path / "nested" / "state.json"))

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == "state.json"
    assert payload["user"]["keyword"] == "AI intern"
    assert payload["jobs"] == []


def test_cli_history_prints_recent_runs(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--keyword",
                "AI intern",
                "--target-count",
                "2",
                "--demo",
                "--db-path",
                "agent.db",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["--history", "--db-path", "agent.db"]) == 0

    captured = capsys.readouterr()
    assert "Recent runs" in captured.out
    assert "run-" in captured.out
    assert "valid_jobs=2" in captured.out


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


def test_cli_evaluate_writes_json_output(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--evaluate",
                "--fixture-sites",
                "--json-output",
                "evaluations/result.json",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Evaluation JSON written to:" in captured.out
    payload = json.loads(
        (tmp_path / "evaluations" / "result.json").read_text(encoding="utf-8")
    )
    assert payload["total_tasks"] == 2
    assert payload["success_rate"] == 1.0
    assert len(payload["task_results"]) == 2


def test_cli_evaluate_seed_url_fixture_writes_single_task_result(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--evaluate",
                "--fixture-sites",
                "--seed-url",
                PUBLIC_JOB_FIXTURE_PAGES[0].url,
                "--json-output",
                "evaluations/seed-result.json",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Completed tasks: 1/1" in captured.out
    payload = json.loads(
        (tmp_path / "evaluations" / "seed-result.json").read_text(encoding="utf-8")
    )
    assert payload["total_tasks"] == 1
    assert payload["completed_tasks"] == 1
    assert payload["task_results"][0]["pages_visited"] == 1


def test_cli_evaluate_can_use_llm_extractor_demo_for_unstructured_seed_url(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--evaluate",
                "--seed-url",
                "https://example.com/jobs/unstructured-ai-agent-intern",
                "--llm-extractor-demo",
                "--json-output",
                "evaluations/unstructured-llm-result.json",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "LLM extractor demo: enabled" in captured.out
    assert "Completed tasks: 1/1" in captured.out
    payload = json.loads(
        (tmp_path / "evaluations" / "unstructured-llm-result.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["completed_tasks"] == 1
    assert payload["total_valid_jobs"] == 1


def test_cli_compare_llm_extractor_writes_comparison_json(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--compare-llm-extractor",
                "--json-output",
                "evaluations/llm-comparison.json",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "LLM extractor comparison" in captured.out
    assert "baseline: 0/1" in captured.out
    assert "llm-demo: 1/1" in captured.out
    payload = json.loads(
        (tmp_path / "evaluations" / "llm-comparison.json").read_text(encoding="utf-8")
    )
    assert payload["baseline"]["completed_tasks"] == 0
    assert payload["llm_demo"]["completed_tasks"] == 1


def test_cli_evaluate_seed_url_fixture_reports_missing_url_details(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--evaluate",
                "--fixture-sites",
                "--seed-url",
                "https://boards.greenhouse.io/example/jobs/missing",
                "--json-output",
                "evaluations/missing-seed-result.json",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Completed tasks: 0/1" in captured.out
    payload = json.loads(
        (tmp_path / "evaluations" / "missing-seed-result.json").read_text(
            encoding="utf-8"
        )
    )
    task_result = payload["task_results"][0]
    assert task_result["failure_category"] == "browser_error"
    assert "https://boards.greenhouse.io/example/jobs/missing" in task_result[
        "failure_details"
    ]
    assert "ValueError" in task_result["failure_details"]


def test_cli_seed_url_dashboard_includes_failed_url_error_trace(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "--seed-url",
                "https://example.com/jobs/missing",
                "--target-count",
                "1",
                "--demo",
                "--dashboard",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Valid jobs: 0" in captured.out
    html = next(Path("dashboards").glob("*.html")).read_text(encoding="utf-8")
    assert "URL Errors" in html
    assert "https://example.com/jobs/missing" in html
    assert "ValueError" in html


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
