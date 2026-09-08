from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

from web_task_agent.models import RunMetrics
from web_task_agent.streamlit_runner import (
    DEFAULT_AGGREGATOR_URL,
    UiRequestError,
    UiRunRequest,
    UiRunResult,
    decode_resume_upload,
    parse_seed_urls,
    parse_skills,
    read_download_artifact,
    run_ui_request,
    sync_provider_secrets,
)

PRIORITY_LABELS = {"high": "高", "medium": "中", "low": "低"}
ARTIFACT_SPECS = {
    "json": ("下载 JSON", "application/json"),
    "report": ("下载 Markdown 报告", "text/markdown"),
    "dashboard": ("下载 HTML Dashboard", "text/html"),
    "action_plan": ("下载行动计划", "text/markdown"),
}


def job_result_rows(result: UiRunResult) -> list[dict[str, Any]]:
    matches = {match.job_id: match for match in result.matches}
    rows: list[dict[str, Any]] = []
    for job in result.jobs:
        match = matches.get(job.url)
        rows.append(
            {
                "岗位": job.title,
                "公司": job.company,
                "地点": job.location,
                "匹配分": match.score if match else 0.0,
                "优先级": PRIORITY_LABELS.get(match.priority, match.priority) if match else "-",
                "匹配技能": "、".join(match.matched_skills) if match else "-",
                "缺失技能": "、".join(match.missing_skills) if match else "-",
                "岗位链接": job.url,
            }
        )
    return rows


def diagnostic_rows(result: UiRunResult) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in result.diagnostics:
        raw_error = str(item.get("error", "unknown"))
        category, separator, detail = raw_error.partition(":")
        rows.append(
            {
                "类别": category.strip(),
                "目标": str(item.get("url", "")),
                "详情": detail.strip() if separator else raw_error,
            }
        )
    return rows


def artifact_download_spec(
    result: UiRunResult,
    artifact_key: str,
) -> tuple[str, str, str, bytes]:
    label, mime = ARTIFACT_SPECS[artifact_key]
    file_name, content = read_download_artifact(result, artifact_key)
    return label, file_name, mime, content


def demo_mode_notice(result: UiRunResult) -> str:
    """Demo 模式结果必须诚实标注:数据是内置夹具,不是真实岗位。"""
    if result.data_mode != "demo":
        return ""
    return (
        "⚠️ **演示数据**：本次结果来自内置夹具页面（example.com），不是真实岗位。"
        "要跑真实招聘数据，请在左侧数据模式中切换为「聚合岗位（上传/URL）」。"
    )


def metric_grid_html(metrics: RunMetrics) -> str:
    values = (
        ("有效岗位", metrics.valid_jobs),
        ("访问页面", metrics.pages_visited),
        ("失败页面", metrics.failed_pages),
        ("重复岗位", metrics.duplicate_jobs),
    )
    items = "".join(
        f'<div class="ui-metric"><span>{label}</span><strong>{value}</strong></div>'
        for label, value in values
    )
    return f"""
<style>
.ui-metric-grid {{
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0.75rem;
  margin-bottom: 1rem;
}}
.ui-metric {{
  border: 1px solid #D8DEE7;
  border-radius: 6px;
  background: #FFFFFF;
  padding: 0.75rem;
}}
.ui-metric span {{ display: block; color: #5F6B7A; font-size: 0.8rem; }}
.ui-metric strong {{ display: block; color: #17202A; font-size: 1.45rem; }}
@media (max-width: 640px) {{
  .ui-metric-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
}}
</style>
<div class="ui-metric-grid">{items}</div>
"""


def main() -> None:
    import streamlit as st

    sync_provider_secrets()
    st.set_page_config(page_title="岗位 Agent 运行台", layout="wide")
    st.title("岗位 Agent 运行台")

    with st.sidebar:
        st.subheader("运行设置")
        mode_label = st.segmented_control(
            "数据模式",
            ["内置 Demo", "聚合岗位（上传/URL）", "指定岗位 URL"],
            default="内置 Demo",
        )
        if mode_label == "内置 Demo":
            st.caption("内置夹具数据,岗位链接指向 example.com,仅用于演示链路。")
        use_llm_extractor = st.toggle("LLM 抽取", value=False)
        extractor_provider = (
            st.selectbox("抽取模型", ["qwen", "deepseek"])
            if use_llm_extractor
            else None
        )
        use_llm_match = st.toggle("LLM 匹配", value=False)
        match_provider = (
            st.selectbox("匹配模型", ["qwen", "deepseek"])
            if use_llm_match
            else None
        )

    with st.form("agent-run-form"):
        first, second = st.columns(2)
        with first:
            keyword = st.text_input("岗位关键词", value="AI Agent 实习")
            skills_text = st.text_input("技能标签", value="Python, LangGraph, RAG")
        with second:
            location = st.text_input("地点", value="全国")
            target_count = st.number_input(
                "目标岗位数", min_value=1, max_value=50, value=10, step=1
            )

        resume_text = st.text_area("简历文本", height=180)
        resume_upload = st.file_uploader("简历文件", type=["md", "txt"])
        aggregator_upload = None
        aggregator_url_text = ""
        seed_url_text = ""
        if mode_label == "聚合岗位（上传/URL）":
            st.caption(
                "两种数据来源二选一：上传本地聚合 JSON，或直接拉取聚合仓库发布的"
                " jobs.json（默认已填 job-radar 真实岗位库）。"
            )
            aggregator_upload = st.file_uploader("岗位聚合文件", type=["json"])
            aggregator_url_text = st.text_input("聚合数据 URL", value=DEFAULT_AGGREGATOR_URL)
        elif mode_label == "指定岗位 URL":
            seed_url_text = st.text_area("岗位 URL", height=120)
        submitted = st.form_submit_button("开始搜索", type="primary", width="stretch")

    if submitted:
        temporary_path: Path | None = None
        try:
            combined_resume = resume_text.strip()
            if resume_upload is not None:
                uploaded_resume = decode_resume_upload(resume_upload.getvalue())
                combined_resume = "\n\n".join(
                    chunk for chunk in (combined_resume, uploaded_resume) if chunk
                )
            if aggregator_upload is not None:
                with tempfile.NamedTemporaryFile(
                    prefix="job-agent-", suffix=".json", delete=False
                ) as temporary_file:
                    temporary_file.write(aggregator_upload.getvalue())
                    temporary_path = Path(temporary_file.name)

            request = UiRunRequest(
                keyword=keyword,
                location=location,
                target_count=int(target_count),
                skills=parse_skills(skills_text),
                resume_text=combined_resume,
                data_mode={
                    "内置 Demo": "demo",
                    "聚合岗位（上传/URL）": "aggregator",
                    "指定岗位 URL": "seed_urls",
                }[mode_label or "内置 Demo"],
                aggregator_path=str(temporary_path) if temporary_path else None,
                aggregator_url=aggregator_url_text.strip() or None,
                seed_urls=parse_seed_urls(seed_url_text),
                llm_extractor_provider=extractor_provider,
                llm_match_provider=match_provider,
            )
            with st.spinner("Agent 正在运行..."):
                st.session_state["latest_ui_result"] = asyncio.run(
                    run_ui_request(request, environ=os.environ)
                )
        except (UiRequestError, ValueError, OSError) as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"任务运行失败：{type(exc).__name__}: {exc}")
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    result = st.session_state.get("latest_ui_result")
    if isinstance(result, UiRunResult):
        _render_result(st, result)


def _render_result(st: Any, result: UiRunResult) -> None:
    st.subheader("运行结果")
    notice = demo_mode_notice(result)
    if notice:
        st.warning(notice)
    st.markdown(metric_grid_html(result.metrics), unsafe_allow_html=True)

    jobs_tab, diagnostics_tab, trace_tab, downloads_tab = st.tabs(
        ["岗位结果", "失败与诊断", "执行轨迹", "下载"]
    )
    with jobs_tab:
        rows = job_result_rows(result)
        if rows:
            st.dataframe(
                rows,
                width="stretch",
                hide_index=True,
                column_config={
                    "匹配分": st.column_config.ProgressColumn(min_value=0.0, max_value=1.0),
                    "岗位链接": st.column_config.LinkColumn(display_text="打开"),
                },
            )
        else:
            st.info("未找到有效岗位。")
    with diagnostics_tab:
        rows = diagnostic_rows(result)
        if rows:
            st.dataframe(rows, width="stretch", hide_index=True)
        else:
            st.success("本次运行没有页面级失败。")
    with trace_tab:
        if result.execution_trace:
            st.dataframe(result.execution_trace, width="stretch", hide_index=True)
        else:
            st.info("本次运行没有执行轨迹。")
    with downloads_tab:
        for artifact_key in ARTIFACT_SPECS:
            if artifact_key not in result.artifacts:
                continue
            label, file_name, mime, content = artifact_download_spec(result, artifact_key)
            st.download_button(
                label,
                data=content,
                file_name=file_name,
                mime=mime,
                key=f"download-{result.run_id}-{artifact_key}",
            )


if __name__ == "__main__":
    main()
