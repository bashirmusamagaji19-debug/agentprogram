from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import streamlit as st

from web_task_agent.open_search.api import _fixture_provider
from web_task_agent.open_search.pipeline import OpenSearchPipeline
from web_task_agent.open_search.query_parser import DemoQueryParser
from web_task_agent.open_search.search_provider import (
    SearchProviderConfigurationError,
    TavilySearchProvider,
)


def _run_search(query: str, mode: str):
    intent = DemoQueryParser().parse(query)
    if mode == "demo":
        provider = _fixture_provider()
    else:
        # Streamlit Cloud exposes Secrets through st.secrets rather than env vars.
        tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
        if not tavily_key:
            try:
                tavily_key = str(st.secrets.get("TAVILY_API_KEY", "")).strip()
            except Exception:
                tavily_key = ""
        if not tavily_key:
            raise SearchProviderConfigurationError("TAVILY_API_KEY is required for online search")
        provider = TavilySearchProvider(tavily_key)
    output_dir = Path(os.getenv("OPEN_SEARCH_ARTIFACT_DIR", "outputs/open-search-runs")) / "streamlit"
    result = asyncio.run(
        OpenSearchPipeline(provider).run(intent, output_dir=output_dir, limit=intent.target_count)
    )
    return intent, result


st.set_page_config(page_title="Open Web Job Search Agent", page_icon="🔎", layout="wide")
st.title("开放互联网岗位搜索 Agent")
st.caption("输入岗位需求，Agent 将解析意图、搜索来源并展示可审计结果。")

query = st.text_area("岗位需求", "北京 Python LangGraph Agent 实习，3 个岗位", height=90)
mode = st.radio("运行模式", ["demo", "online"], format_func=lambda value: "离线演示" if value == "demo" else "开放互联网搜索", horizontal=True)

if st.button("开始搜索", type="primary", disabled=not query.strip()):
    with st.spinner("正在解析需求并检索岗位..."):
        try:
            intent, result = _run_search(query.strip(), mode)
        except SearchProviderConfigurationError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"运行失败：{type(exc).__name__}: {exc}")
        else:
            summary = result.summary
            with st.expander("查看解析后的搜索意图", expanded=True):
                st.json(intent.model_dump(mode="json"))
            cols = st.columns(4)
            cols[0].metric("候选来源", summary.candidates_seen)
            cols[1].metric("已验证岗位", summary.verified_count)
            cols[2].metric("失败数", summary.failures)
            cols[3].metric("结束原因", summary.terminal_reason)
            for index, job in enumerate(result.jobs, start=1):
                with st.container(border=True):
                    st.subheader(f"{index}. {job.title}")
                    st.write(f"**公司：** {job.company}　**地点：** {job.location}")
                    st.write(f"**来源：** {job.source}")
                    st.write(f"**链接：** [{job.url}]({job.url})")
                    if job.requirements:
                        st.write(f"**要求：** {job.requirements}")
                    with st.expander("查看字段证据"):
                        for evidence in job.evidence:
                            st.write(f"`{evidence.field_name}` · {evidence.page_url}")
                            st.code(evidence.content_hash, language="text")
            if result.failures:
                with st.expander("查看失败记录"):
                    for failure in result.failures:
                        st.warning(f"{failure.code}: {failure.message} ({failure.url})")
            payload = {
                "intent": intent.model_dump(mode="json"),
                "summary": summary.model_dump(mode="json"),
                "jobs": [job.model_dump(mode="json") for job in result.jobs],
                "failures": [failure.model_dump(mode="json") for failure in result.failures],
            }
            st.download_button(
                "下载本次结构化结果",
                data=json.dumps(payload, ensure_ascii=False, indent=2),
                file_name="open-search-result.json",
                mime="application/json",
            )
