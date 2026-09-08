from __future__ import annotations

import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, Field

from web_task_agent.action_plan import ActionPlanWriter
from web_task_agent.browser import BrowserUseClient, FakeBrowserClient, HttpPageLoader
from web_task_agent.dashboard import HtmlDashboard
from web_task_agent.demo_pages import DEMO_JOB_PAGES
from web_task_agent.extractor import PageExtractor
from web_task_agent.job_sources import AggregatorPageLoader, AggregatorRepoSource
from web_task_agent.llm_extractor import (
    build_configured_llm_field_extractor,
    build_configured_llm_matcher,
)
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import JobPosting, MatchResult, RunMetrics, UserProfile
from web_task_agent.official_api import OfficialApiContentFetcher
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow

UiDataMode = Literal["demo", "aggregator", "seed_urls"]

PROVIDER_API_KEY_ENV = {
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
}


def sync_provider_secrets(environ: dict[str, str] | None = None) -> None:
    """把 Streamlit Secrets 中的 provider key 补进进程环境变量。

    Streamlit Community Cloud 的 Secrets 不会自动注入 os.environ,而
    build_configured_llm_* 在构建时直接读 os.environ,所以要在应用启动时
    同步一次。环境变量优先:已配置的值不会被 secrets 改写。
    本地无 secrets.toml 或无 streamlit 运行时时静默跳过。
    """
    target = os.environ if environ is None else environ
    try:
        import streamlit as st

        secrets: Mapping[str, Any] = dict(st.secrets)
    except Exception:
        return
    for env_name in PROVIDER_API_KEY_ENV.values():
        if not str(target.get(env_name, "")).strip() and env_name in secrets:
            target[env_name] = str(secrets[env_name])


class UiRequestError(ValueError):
    """A safe validation error suitable for display in the Web UI."""


class UiRunRequest(BaseModel):
    keyword: str = "AI Agent 实习"
    location: str = "全国"
    target_count: int = Field(default=10, ge=1, le=50)
    skills: list[str] = Field(default_factory=list)
    resume_text: str = ""
    data_mode: UiDataMode = "demo"
    aggregator_path: str | None = None
    seed_urls: list[str] = Field(default_factory=list)
    llm_extractor_provider: Literal["deepseek", "qwen"] | None = None
    llm_match_provider: Literal["deepseek", "qwen"] | None = None


class UiRunResult(BaseModel):
    run_id: str
    jobs: list[JobPosting] = Field(default_factory=list)
    matches: list[MatchResult] = Field(default_factory=list)
    metrics: RunMetrics
    failed_urls: list[str] = Field(default_factory=list)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    execution_trace: list[dict[str, str]] = Field(default_factory=list)
    artifacts: dict[str, Path] = Field(default_factory=dict)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = value.strip()
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def parse_skills(value: str) -> list[str]:
    return _unique(re.split(r"[,，\n;；]+", value))


def parse_seed_urls(value: str) -> list[str]:
    return _unique(re.split(r"[\s,，]+", value))


def decode_resume_upload(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise UiRequestError("简历文件必须使用 UTF-8 编码。") from exc


def validate_ui_request(
    request: UiRunRequest,
    *,
    environ: Mapping[str, str],
) -> None:
    if request.data_mode == "aggregator" and not request.aggregator_path:
        raise UiRequestError("请上传聚合岗位 JSON 文件。")
    if request.data_mode == "seed_urls":
        if not request.seed_urls:
            raise UiRequestError("请至少填写一个 HTTP(S) 岗位 URL。")
        invalid = [url for url in request.seed_urls if not _is_http_url(url)]
        if invalid:
            raise UiRequestError("指定岗位 URL 必须是有效的 HTTP(S) 地址。")

    providers = {
        provider
        for provider in (
            request.llm_extractor_provider,
            request.llm_match_provider,
        )
        if provider
    }
    missing = sorted(
        PROVIDER_API_KEY_ENV[provider]
        for provider in providers
        if not environ.get(PROVIDER_API_KEY_ENV[provider], "").strip()
    )
    if missing:
        raise UiRequestError(f"缺少模型环境变量：{', '.join(missing)}")


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


async def run_ui_request(
    request: UiRunRequest,
    *,
    output_root: str | Path = "streamlit-runs",
    environ: Mapping[str, str] | None = None,
    page_loader: Callable[[str], Awaitable[Any]] | None = None,
) -> UiRunResult:
    current_environ = os.environ if environ is None else environ
    validate_ui_request(request, environ=current_environ)

    run_id = f"run-{uuid4().hex[:8]}"
    run_dir = Path(output_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    repository = JobRepository(run_dir / "agent.db")
    repository.initialize()

    browser, seed_urls = await _build_browser(
        request,
        repository=repository,
        page_loader=page_loader,
    )
    user = UserProfile(
        keyword=request.keyword,
        location=request.location,
        target_count=request.target_count,
        skills=request.skills,
        resume_text=request.resume_text,
        seed_urls=seed_urls,
    )
    workflow = WebTaskWorkflow(
        browser=browser,
        extractor=PageExtractor(
            llm_field_extractor=(
                build_configured_llm_field_extractor(
                    provider=request.llm_extractor_provider,
                )
                if request.llm_extractor_provider
                else None
            )
        ),
        matcher=JobMatcher(
            llm_matcher=(
                build_configured_llm_matcher(provider=request.llm_match_provider)
                if request.llm_match_provider
                else None
            )
        ),
        verifier=JobVerifier(),
        repository=repository,
        reporter=MarkdownReporter(run_dir / "reports"),
    )
    state = await workflow.run_with_langgraph(user=user, run_id=run_id)
    if state.metrics is None:
        raise RuntimeError("Agent run completed without metrics")

    action_plan_path = ActionPlanWriter(run_dir / "action-plans").write_plan(
        run_id=run_id,
        user=user,
        jobs=state.jobs,
        matches=state.matches,
    )
    dashboard_path = HtmlDashboard(run_dir / "dashboards").write_dashboard(
        user=user,
        jobs=state.jobs,
        matches=state.matches,
        metrics=state.metrics,
        search_queries=state.search_queries,
        failed_url_errors=state.metadata.get("failed_url_errors", []),
        artifact_links={
            "Markdown 报告": state.report_path or "",
            "行动计划": action_plan_path,
        },
        execution_trace=state.metadata.get("execution_trace", []),
        orchestration_mode=str(state.metadata.get("orchestration_mode", "langgraph")),
    )
    json_path = run_dir / "result.json"
    json_path.write_text(
        json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    diagnostics = list(state.metadata.get("failed_url_errors", []))
    diagnostics.extend(
        {
            "url": str(item.get("url", "")),
            "error": "verification_filtered: " + ", ".join(item.get("reasons", [])),
        }
        for item in state.metadata.get("filtered_jobs", [])
    )
    return UiRunResult(
        run_id=run_id,
        jobs=state.jobs,
        matches=state.matches,
        metrics=state.metrics,
        failed_urls=state.failed_urls,
        diagnostics=diagnostics,
        execution_trace=state.metadata.get("execution_trace", []),
        artifacts={
            "json": json_path,
            "report": Path(state.report_path or ""),
            "dashboard": dashboard_path,
            "action_plan": action_plan_path,
        },
    )


async def _build_browser(
    request: UiRunRequest,
    *,
    repository: JobRepository,
    page_loader: Callable[[str], Awaitable[Any]] | None,
) -> tuple[FakeBrowserClient | BrowserUseClient, list[str]]:
    if request.data_mode == "demo":
        return FakeBrowserClient(DEMO_JOB_PAGES), []
    if request.data_mode == "seed_urls":
        return BrowserUseClient(page_loader=page_loader or HttpPageLoader()), request.seed_urls

    source = AggregatorRepoSource(request.aggregator_path or "")
    jobs = await source.discover(limit=request.target_count)
    loader = AggregatorPageLoader(
        jobs,
        official_api=OfficialApiContentFetcher(),
        http_loader=page_loader or HttpPageLoader(),
        repository=repository,
    )
    return BrowserUseClient(page_loader=loader), [job.url for job in jobs]


def read_download_artifact(result: UiRunResult, artifact_key: str) -> tuple[str, bytes]:
    path = result.artifacts.get(artifact_key)
    if path is None or not path.is_file():
        raise UiRequestError("该产物不可下载。")
    return path.name, path.read_bytes()
