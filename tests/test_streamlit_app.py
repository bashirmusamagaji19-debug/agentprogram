from __future__ import annotations

from pathlib import Path

from web_task_agent.models import JobPosting, MatchResult, RunMetrics
from web_task_agent.streamlit_app import (
    artifact_download_spec,
    diagnostic_rows,
    job_result_rows,
)
from web_task_agent.streamlit_runner import UiRunResult


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
