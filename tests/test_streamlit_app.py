from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from web_task_agent.models import JobPosting, MatchResult, RunMetrics
from web_task_agent.streamlit_app import (
    artifact_download_spec,
    diagnostic_rows,
    job_result_rows,
    metric_grid_html,
)
from web_task_agent.streamlit_runner import (
    PROVIDER_API_KEY_ENV,
    UiRunResult,
    sync_provider_secrets,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_result(tmp_path: Path) -> UiRunResult:
    report = tmp_path / "report.md"
    report.write_text("# report", encoding="utf-8")
    return UiRunResult(
        run_id="run-ui",
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
