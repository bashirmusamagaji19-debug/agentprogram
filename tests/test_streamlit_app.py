from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from web_task_agent.models import JobPosting, MatchResult, RunMetrics
from web_task_agent.streamlit_app import (
    artifact_download_spec,
    artifact_cards_html,
    diagnostic_rows,
    job_result_rows,
    metric_grid_html,
    mode_hint,
    run_status,
)
from web_task_agent.streamlit_runner import (
    DEFAULT_AGGREGATOR_URL,
    PROVIDER_API_KEY_ENV,
    UiRequestError,
    UiRunRequest,
    UiRunResult,
    resolve_aggregator_location,
    sync_provider_secrets,
    validate_ui_request,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_result(tmp_path: Path, data_mode: str = "demo") -> UiRunResult:
    report = tmp_path / "report.md"
    report.write_text("# report", encoding="utf-8")
    return UiRunResult(
        run_id="run-ui",
        data_mode=data_mode,
        jobs=[
            JobPosting(
                title="AI Agent 实习生",
                company="示例科技",
                location="北京",
                source="fixture",
                url="https://example.com/jobs/1",
                skills=["Python", "RAG"],
                confidence=0.9,
            )
        ],
        matches=[
            MatchResult(
                job_id="https://example.com/jobs/1",
                score=0.75,
                priority="high",
                matched_skills=["Python"],
                missing_skills=["RAG"],
            )
        ],
        metrics=RunMetrics(run_id="run-ui", valid_jobs=1),
        diagnostics=[
            {"url": "https://example.com/jobs/2", "error": "empty_page: no text"}
        ],
        execution_trace=[{"node": "planner", "summary": "planned 3 queries"}],
        artifacts={"report": report},
    )


def test_job_result_rows_join_jobs_with_match_results(tmp_path: Path) -> None:
    rows = job_result_rows(make_result(tmp_path))

    assert rows == [
        {
            "岗位": "AI Agent 实习生",
            "公司": "示例科技",
            "页面核验": "—",
            "梯队": "—",
            "类别": "—",
            "地点": "北京",
            "匹配分": 0.75,
            "优先级": "高",
            "匹配技能": "Python",
            "缺失技能": "RAG",
            "岗位链接": "https://example.com/jobs/1",
        }
    ]


def test_diagnostic_rows_preserve_category_and_url(tmp_path: Path) -> None:
    assert diagnostic_rows(make_result(tmp_path)) == [
        {
            "类别": "empty_page",
            "目标": "https://example.com/jobs/2",
            "详情": "no text",
        }
    ]


def test_artifact_download_spec_uses_safe_label_and_mime(tmp_path: Path) -> None:
    result = make_result(tmp_path)

    label, file_name, mime, content = artifact_download_spec(result, "report")

    assert label == "下载 Markdown 报告"
    assert file_name == "report.md"
    assert mime == "text/markdown"
    assert content == b"# report"


def test_metric_grid_switches_to_two_columns_on_mobile() -> None:
    html = metric_grid_html(RunMetrics(run_id="run-ui", valid_jobs=3, failed_pages=1))

    assert "repeat(4, minmax(0, 1fr))" in html
    assert "@media (max-width: 640px)" in html
    assert "repeat(2, minmax(0, 1fr))" in html
    assert "有效岗位" in html
    assert ">3<" in html


def test_run_status_distinguishes_ready_success_and_partial_failure(tmp_path: Path) -> None:
    assert run_status(None) == ("待运行", "配置任务后开始一次岗位扫描", "ready")
    assert run_status(make_result(tmp_path)) == ("已完成", "1 个有效岗位 · 1 个页面失败", "warning")


def test_mode_hint_explains_each_data_mode() -> None:
    assert "内置夹具" in mode_hint("demo")
    assert "官方招聘 API" in mode_hint("official")
    assert "聚合岗位" in mode_hint("aggregator")
    assert "指定岗位 URL" in mode_hint("seed_urls")


def test_metric_grid_contains_status_classes_and_accessible_labels() -> None:
    html = metric_grid_html(RunMetrics(run_id="run-ui", valid_jobs=3, failed_pages=1))

    assert 'class="ui-metric-grid"' in html
    assert 'class="ui-metric ui-metric-success"' in html
    assert 'class="ui-metric ui-metric-warning"' in html
    assert 'aria-label="有效岗位 3"' in html


def test_artifact_cards_html_lists_available_downloads(tmp_path: Path) -> None:
    result = make_result(tmp_path)

    html = artifact_cards_html(result)

    assert "Markdown 报告" in html
    assert "本次运行的可复核结果" in html
    assert "report.md" in html


def test_cloud_requirements_cover_runtime_deps_without_browser_use() -> None:
    lines = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    names = {
        part.strip().lower()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
        for part in [line.split(";")[0].split(">=")[0].split("<")[0].split("==")[0]]
    }

    assert {"streamlit", "langgraph", "pydantic", "python-dotenv"} <= names
    assert not any("browser-use" in name or "browser_use" in name for name in names)


def test_root_entry_injects_src_into_sys_path(monkeypatch) -> None:
    src_dir = str(REPO_ROOT / "src")
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != src_dir])

    spec = importlib.util.spec_from_file_location(
        "root_streamlit_entry", REPO_ROOT / "streamlit_app.py"
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert sys.path[0] == src_dir
    assert callable(module.main)


def test_sync_provider_secrets_fills_missing_env(monkeypatch) -> None:
    import streamlit as st

    monkeypatch.setattr(
        st, "secrets", dict.fromkeys(PROVIDER_API_KEY_ENV.values(), "sk-from-secrets"),
        raising=False,
    )

    environ: dict[str, str] = {}
    sync_provider_secrets(environ)

    assert environ == dict.fromkeys(PROVIDER_API_KEY_ENV.values(), "sk-from-secrets")


def test_sync_provider_secrets_keeps_existing_env(monkeypatch) -> None:
    import streamlit as st

    env_name = next(iter(PROVIDER_API_KEY_ENV.values()))
    monkeypatch.setattr(st, "secrets", {env_name: "sk-from-secrets"}, raising=False)

    environ = {env_name: "sk-from-env"}
    sync_provider_secrets(environ)

    assert environ[env_name] == "sk-from-env"


def test_sync_provider_secrets_ignores_missing_secrets_file(monkeypatch) -> None:
    import streamlit as st

    class MissingSecrets:
        def keys(self):  # noqa: ANN001
            raise FileNotFoundError("No secrets.toml found")

    monkeypatch.setattr(st, "secrets", MissingSecrets(), raising=False)

    environ: dict[str, str] = {}
    sync_provider_secrets(environ)

    assert environ == {}


def test_validate_aggregator_mode_accepts_url_only() -> None:
    validate_ui_request(
        UiRunRequest(
            data_mode="aggregator",
            aggregator_url=DEFAULT_AGGREGATOR_URL,
        ),
        environ={},
    )


def test_validate_aggregator_mode_requires_file_or_url() -> None:
    import pytest

    with pytest.raises(UiRequestError, match="上传.*或.*URL"):
        validate_ui_request(UiRunRequest(data_mode="aggregator"), environ={})


def test_validate_aggregator_mode_rejects_non_http_url() -> None:
    import pytest

    with pytest.raises(UiRequestError, match="HTTP"):
        validate_ui_request(
            UiRunRequest(data_mode="aggregator", aggregator_url="ftp://example.com/j.json"),
            environ={},
        )


def test_resolve_aggregator_location_prefers_uploaded_file() -> None:
    location = resolve_aggregator_location(
        UiRunRequest(
            data_mode="aggregator",
            aggregator_path="C:/tmp/jobs.json",
            aggregator_url=DEFAULT_AGGREGATOR_URL,
        )
    )

    assert location == "C:/tmp/jobs.json"


def test_resolve_aggregator_location_falls_back_to_url() -> None:
    location = resolve_aggregator_location(
        UiRunRequest(data_mode="aggregator", aggregator_url=DEFAULT_AGGREGATOR_URL)
    )

    assert location == DEFAULT_AGGREGATOR_URL


def test_default_aggregator_url_is_job_radar_raw_json() -> None:
    assert DEFAULT_AGGREGATOR_URL.startswith("https://")
    assert DEFAULT_AGGREGATOR_URL.endswith("jobs.json")


def test_ui_run_result_carries_data_mode(tmp_path: Path) -> None:
    result = make_result(tmp_path)

    assert result.data_mode == "demo"


def test_demo_notice_marks_fixture_data(tmp_path: Path) -> None:
    from web_task_agent.streamlit_app import demo_mode_notice

    notice = demo_mode_notice(make_result(tmp_path))

    assert "example.com" in notice
    assert "演示" in notice or "夹具" in notice

    assert demo_mode_notice(make_result(tmp_path, data_mode="aggregator")) == ""
