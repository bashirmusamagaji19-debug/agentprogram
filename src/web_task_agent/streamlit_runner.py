from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, Field

from web_task_agent.action_plan import ActionPlanWriter
from web_task_agent.agent_approval import ApprovalDecision, HitlRuntimeError
from web_task_agent.agent_components import AgentServices
from web_task_agent.agent_run_context import read_run_context, require_run_identity
from web_task_agent.agent_service import AgentRunRequest as UiRunRequest
from web_task_agent.agent_service import resume_agent_run, start_agent_run
from web_task_agent.artifact_store import (
    ArtifactNotFoundError,
    ArtifactRecord,
    ArtifactStore,
)
from web_task_agent.browser import BrowserUseClient, FakeBrowserClient, HttpPageLoader
from web_task_agent.dashboard import HtmlDashboard
from web_task_agent.extractor import PageExtractor
from web_task_agent.job_sources import AggregatorPageLoader, AggregatorRepoSource
from web_task_agent.llm_extractor import (
    build_configured_llm_field_extractor,
    build_configured_llm_matcher,
)
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import JobPosting, MatchResult, RunMetrics, UserProfile
from web_task_agent.official_api import OfficialApiContentFetcher
from web_task_agent.official_list_source import OfficialListSource
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.run_store import RunRecord, RunStore
from web_task_agent.search_conditions import parse_keywords
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow

UiDataMode = Literal["demo", "aggregator", "seed_urls", "official", "web_search"]
RETENTION_DAYS = 7

# job-radar 聚合仓库发布的 jobs.json（GitHub raw，含 official_url/jd_text），
# 云端/本地都可直接拉取——让 Streamlit 入口无需上传文件即可走真实数据链路。
DEFAULT_AGGREGATOR_URL = (
    "https://raw.githubusercontent.com/Jasmine-Liu-min/job-radar/main/data/jobs.json"
)

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


class UiRunResult(BaseModel):
    run_id: str
    data_mode: UiDataMode = "demo"
    jobs: list[JobPosting] = Field(default_factory=list)
    pending_jobs: list[JobPosting] = Field(default_factory=list)
    matches: list[MatchResult] = Field(default_factory=list)
    metrics: RunMetrics
    failed_urls: list[str] = Field(default_factory=list)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    source_diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    search_conditions: dict[str, Any] = Field(default_factory=dict)
    execution_trace: list[dict[str, str]] = Field(default_factory=list)
    artifacts: dict[str, Path] = Field(default_factory=dict)
    artifact_records: dict[str, ArtifactRecord] = Field(default_factory=dict)
    engine: str = "workflow"
    terminal_status: str = ""
    terminal_reason: str = ""
    agent_trace: list[dict[str, Any]] = Field(default_factory=list)
    approval: dict[str, Any] | None = None
    run_status: str = ""
    planner_routes: list[dict[str, Any]] = Field(default_factory=list)
    model_usage: list[dict[str, Any]] = Field(default_factory=list)
    model_usage_complete: bool = False
    unmetered_model_roles: list[str] = Field(default_factory=list)
    unique_candidates: int = 0
    open_successes: int = 0
    open_failures: int = 0
    tool_latency_ms: float = 0.0
    runtime_latency_ms: float = 0.0
    approval_wait_ms: float = 0.0
    budget: dict[str, int] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    saved: bool = False
    resolution_log: list[dict[str, Any]] = Field(default_factory=list)
    source_manifest: str | None = None
    source_mode: str = ""
    source_captured_at: str | None = None
    source_metrics: dict[str, Any] = Field(default_factory=dict)
    source_evidence: dict[str, dict[str, Any]] = Field(default_factory=dict)


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


def decode_resume_upload(content: bytes, *, filename: str = "") -> str:
    if filename.lower().endswith(".pdf") or content.lstrip().startswith(b"%PDF-"):
        from io import BytesIO

        from pypdf import PdfReader

        try:
            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted and not reader.decrypt(""):
                raise UiRequestError("PDF 简历已加密，请先解密后上传。")
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except UiRequestError:
            raise
        except Exception as exc:
            raise UiRequestError("无法读取 PDF 简历，请确认文件未损坏，或复制文字粘贴。") from exc
        if not text:
            raise UiRequestError("PDF 未提取到文字，可能是扫描件，请复制简历文字后粘贴。")
        return text
    try:
        return content.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise UiRequestError("简历文件必须使用 UTF-8 编码。") from exc


def validate_ui_request(
    request: UiRunRequest,
    *,
    environ: Mapping[str, str],
) -> None:
    if request.data_mode == "aggregator":
        has_url = bool((request.aggregator_url or "").strip())
        if not request.aggregator_path and not has_url:
            raise UiRequestError("请上传聚合岗位 JSON 文件，或填写聚合数据 URL。")
        if has_url and not _is_http_url(request.aggregator_url.strip()):
            raise UiRequestError("聚合数据 URL 必须是有效的 HTTP(S) 地址。")
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
            request.planner_provider if request.planner_strategy != "policy" else None,
        )
        if provider
    }
    if request.data_mode == "web_search":
        providers.add("deepseek")
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


def resolve_aggregator_location(request: UiRunRequest) -> str:
    """聚合模式数据位置:上传文件优先,否则用聚合数据 URL。

    URL 场景(AggregatorRepoSource 原生支持 raw URL)让云端演示
    不依赖本地上传即可走真实数据链路。
    """
    if request.aggregator_path:
        return request.aggregator_path
    return (request.aggregator_url or "").strip()


async def run_ui_request(
    request: UiRunRequest,
    *,
    output_root: str | Path = "streamlit-runs",
    environ: Mapping[str, str] | None = None,
    page_loader: Callable[[str], Awaitable[Any]] | None = None,
    link_verifier: Any = None,
    run_store: RunStore | None = None,
    artifact_store: ArtifactStore | None = None,
    owner_id: str | None = None,
    _resume_run_id: str | None = None,
    _approval_decision: ApprovalDecision | None = None,
) -> UiRunResult:
    current_environ = os.environ if environ is None else environ
    if not _resume_run_id:
        validate_ui_request(request, environ=current_environ)
    if (run_store is None) != (artifact_store is None):
        raise ValueError("run_store and artifact_store must be provided together")
    persistence_owner = (owner_id or "demo").strip()
    if not persistence_owner:
        raise ValueError("owner_id must not be blank")

    run_id = require_run_identity(_resume_run_id) if _resume_run_id else f"run-{uuid4().hex[:8]}"
    run_dir = Path(output_root) / run_id
    if _resume_run_id:
        read_run_context(run_dir, run_id=run_id, owner_id=persistence_owner)
    run_dir.mkdir(parents=True, exist_ok=bool(_resume_run_id))
    repository = JobRepository(run_dir / "agent.db")
    repository.initialize()

    created_at = datetime.now(UTC)
    retention_deadline = created_at + timedelta(days=RETENTION_DAYS)
    (run_dir / "retention.json").write_text(json.dumps({
        "run_id": run_id, "retention_deadline": retention_deadline.isoformat()}), encoding="utf-8")
    if run_store is not None and not _resume_run_id:
        run_store.create_run(
            RunRecord(
                run_id=run_id,
                owner_id=persistence_owner,
                status="queued",
                data_mode=request.data_mode,
                request_summary=_safe_request_summary(request),
                created_at=created_at,
                updated_at=created_at,
                retention_deadline=retention_deadline,
            )
        )
        run_store.update_run(
            persistence_owner,
            run_id,
            status="running",
            updated_at=datetime.now(UTC),
        )
    elif run_store is not None and _approval_decision is not None:
        existing = run_store.get_run(persistence_owner, run_id)
        if existing is not None and existing.status == "awaiting_approval" and _approval_decision.outcome == "approve":
            run_store.update_run(persistence_owner, run_id, status="running", updated_at=datetime.now(UTC))

    try:
        if _resume_run_id:
            # The graph is paused immediately before save: no navigation or new
            # discovery is permitted while confirming an already frozen batch.
            browser, seed_urls = FakeBrowserClient([]), request.seed_urls
            previous_path = run_dir / "result.json"
            if previous_path.is_file():
                previous = UiRunResult.model_validate_json(previous_path.read_text(encoding="utf-8"))
                browser.discovered_count = previous.progress.get("discovered", len(seed_urls))
                browser.selected_count = previous.progress.get("selected", len(seed_urls))
                browser.source_diagnostics = previous.source_diagnostics
                browser.resolution_log = previous.resolution_log
                source_reference = {key: getattr(previous, key) for key in (
                    "source_manifest", "source_mode", "source_captured_at")}
                browser.reference_metadata = lambda directory: source_reference
                manifest = _run_artifact_path(run_dir, previous.source_manifest)
                if manifest is not None:
                    from web_task_agent.source_archive import SourceArchive

                    browser.archive = SourceArchive(manifest.parent)
        else:
            try:
                browser, seed_urls = await _build_browser(
                    request, repository=repository, page_loader=page_loader,
                    run_dir=run_dir, run_id=run_id,
                )
            except TimeoutError:
                browser, seed_urls = FakeBrowserClient([]), []
                browser.request_deadline = monotonic()
                browser.source_diagnostics = [{
                    "source": ", ".join(request.official_specs or [request.data_mode]),
                    "status": "failed", "attempts": 1, "count": 0, "candidate_count": 0,
                    "error_detail": "来源请求已达到本次总时限，请稍后重试。",
                    "error_category": "transient",
                }]
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
            enforce_search_conditions=True,
            require_verified_links=request.data_mode != "demo",
            link_verifier=link_verifier,
        )
        services = AgentServices(workflow=workflow, run_dir=run_dir)
        if _approval_decision is not None:
            service_result = await resume_agent_run(
                run_id=run_id, owner_id=persistence_owner,
                approval_decision=_approval_decision, services=services,
            )
        else:
            service_result = await start_agent_run(
                user, run_id=run_id, owner_id=persistence_owner,
                services=services, options=request.options(candidate_count=(
                    10 if request.data_mode == "web_search" else
                    len(seed_urls) if seed_urls else getattr(browser, "discovered_count", None))),
            )
        state = service_result.view
        if hasattr(browser, "finalize"):
            state.metadata.update(browser.finalize_metadata(run_dir))
        source_diagnostics = list(getattr(browser, "source_diagnostics", []))
        resolution_log = list(getattr(browser, "resolution_log", []))
        if _resume_run_id and (run_dir / "result.json").is_file():
            previous = UiRunResult.model_validate_json((run_dir / "result.json").read_text(encoding="utf-8"))
            source_diagnostics = previous.source_diagnostics
            resolution_log = previous.resolution_log
            state.metadata["source_manifest"] = previous.source_manifest
            state.metadata["source_mode"] = previous.source_mode
            state.metadata["source_captured_at"] = previous.source_captured_at
            if "source_metrics" not in state.metadata:
                state.metadata["source_metrics"] = previous.source_metrics
                state.metadata["source_evidence"] = previous.source_evidence
        state.metadata["source_diagnostics"] = source_diagnostics
        state.metadata["resolution_log"] = resolution_log
        if state.metrics is None:
            raise RuntimeError("Agent run completed without metrics")

        source_manifest_path = _run_artifact_path(run_dir, state.metadata.get("source_manifest"))
        source_bundle_path = (
            _bundle_source_archive(source_manifest_path.parent, run_dir)
            if source_manifest_path is not None
            else None
        )
        link_bundle_path = _bundle_link_checks(run_dir)
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
            source_metrics=state.metadata.get("source_metrics"),
            source_evidence=state.metadata.get("source_evidence"),
        )
        diagnostics = list(state.metadata.get("failed_url_errors", []))
        removed_urls = {row["url"] for row in resolution_log if row.get("category") == "removed"}
        diagnostics = [row for row in diagnostics if row.get("url") not in removed_urls]
        diagnostics.extend(
            {"url": str(item["source"]), "error": f"source_{item['status']}: "
             + str(item.get("error_detail", "no jobs returned")), "attempts": item["attempts"]}
            for item in source_diagnostics if item["status"] != "healthy"
        )
        diagnostics.extend(
            {"url": str(item["source"]), "error": "search_filtered: "
             + f"{int(item['count']) - int(item['candidate_count'])} candidates did not match keyword/location",
             "kind": "search_filter"}
            for item in source_diagnostics
            if int(item.get("count", 0)) > int(item.get("candidate_count", item.get("count", 0)))
        )
        page_diagnostics = {
            (row["url"], row["category"]): {
                "url": row["url"], "error": f"page_{row['category']}: {row['detail']}"
            }
            for row in resolution_log if row.get("category")
        }
        diagnostics.extend(page_diagnostics.values())
        diagnostics.extend(
            {
                "url": str(item.get("url", "")),
                "error": "verification_filtered: " + ", ".join(item.get("reasons", [])),
                "kind": item.get("kind", "verification"),
            }
            for item in state.metadata.get("filtered_jobs", [])
        )
        result = UiRunResult(
            run_id=run_id,
            data_mode=request.data_mode,
            engine=request.engine,
            terminal_status=state.metadata.get("terminal_status", ""),
            terminal_reason=state.metadata.get("terminal_reason", ""),
            agent_trace=state.metadata.get("agent_trace", []),
            approval=service_result.approval,
            run_status=service_result.status,
            planner_routes=state.metadata.get("planner_routes", []),
            model_usage=state.metadata.get("model_usage", []),
            model_usage_complete=state.metadata.get("model_usage_complete", False),
            unmetered_model_roles=state.metadata.get("unmetered_model_roles", []),
            unique_candidates=state.metadata.get("unique_candidates", 0),
            open_successes=state.metadata.get("open_successes", 0),
            open_failures=state.metadata.get("open_failures", 0),
            tool_latency_ms=state.metadata.get("tool_latency_ms", 0.0),
            runtime_latency_ms=state.metadata.get("runtime_latency_ms", 0.0),
            approval_wait_ms=state.metadata.get("approval_wait_ms", 0.0),
            budget=({**service_result.agent_state.budget.model_dump(mode="json"),
                     "remaining_steps": service_result.agent_state.budget.remaining_steps}
                    if service_result.agent_state is not None else {}),
            saved=bool(service_result.agent_state.saved) if service_result.agent_state else bool(state.jobs),
            progress={
                "discovered": getattr(browser, "discovered_count", len(seed_urls)),
                "selected": (getattr(browser, "selected_count", 0)
                             if request.data_mode == "web_search" else len(seed_urls)), "limit": 10,
                "checked": len(service_result.agent_state.visited_urls | service_result.agent_state.rejected_urls)
                if service_result.agent_state else state.metrics.pages_visited,
                "verified": (len(service_result.agent_state.verified_jobs)
                             if service_result.agent_state else len(state.jobs)),
                "delivered": len(state.jobs) if (
                    service_result.agent_state.saved if service_result.agent_state else bool(state.jobs)) else 0,
                "pending": len(state.pending_jobs),
                "unexamined": max(0, getattr(browser, "discovered_count", len(seed_urls)) - (
                    len(service_result.agent_state.visited_urls | service_result.agent_state.rejected_urls)
                    if service_result.agent_state else state.metrics.pages_visited)),
            },
            jobs=state.jobs,
            pending_jobs=state.pending_jobs,
            matches=state.matches,
            metrics=state.metrics,
            failed_urls=state.failed_urls,
            diagnostics=diagnostics,
            source_diagnostics=source_diagnostics,
            resolution_log=resolution_log,
            source_manifest=state.metadata.get("source_manifest"),
            source_mode=state.metadata.get("source_mode", ""),
            source_captured_at=state.metadata.get("source_captured_at"),
            source_metrics=state.metadata.get("source_metrics", {}),
            source_evidence=state.metadata.get("source_evidence", {}),
            search_conditions={
                "keyword": request.keyword, "keywords": parse_keywords(request.keyword),
                "location": request.location, "keyword_logic": "all", "location_logic": "any",
                "candidate_budget": 10,
            },
            execution_trace=state.metadata.get("execution_trace", []),
            artifacts={
                "json": run_dir / "result.json",
                "report": Path(state.report_path or ""),
                "dashboard": dashboard_path,
                "action_plan": action_plan_path,
                **({"source_manifest": source_manifest_path}
                   if source_manifest_path is not None else {}),
                **({"source_archive": source_bundle_path}
                   if source_bundle_path is not None else {}),
                **({"link_checks": link_bundle_path} if link_bundle_path is not None else {}),
            },
        )
        json_path = result.artifacts["json"]
        json_path.write_text(
            json.dumps(
                result.model_dump(
                    mode="json",
                    exclude={"artifacts", "artifact_records"},
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if artifact_store is not None:
            result.artifact_records = _persist_artifacts(
                artifact_store=artifact_store,
                owner_id=persistence_owner,
                run_id=run_id,
                artifacts=result.artifacts,
                retention_deadline=retention_deadline,
            )
        if run_store is not None:
            run_status = _terminal_run_status(
                failed_pages=state.metrics.failed_pages,
                diagnostics=diagnostics,
            )
            if request.engine == "hybrid":
                run_status = service_result.status
            run_store.update_run(
                persistence_owner,
                run_id,
                status=run_status,
                metrics=state.metrics.model_dump(mode="json"),
                error_summary=_safe_terminal_summary(
                    run_status,
                    failed_pages=state.metrics.failed_pages,
                    diagnostics=diagnostics,
                ),
                updated_at=datetime.now(UTC),
                retention_deadline=retention_deadline,
            )
        return result
    except Exception as exc:
        if "browser" in locals() and hasattr(browser, "finalize"):
            browser.finalize()
        if run_store is not None:
            # A resume may have committed the approved batch or advanced its
            # checkpoint before an I/O failure. Keep its status reconcilable;
            # the service checkpoint decides the terminal outcome on retry.
            current = run_store.get_run(persistence_owner, run_id) if _resume_run_id else None
            run_store.update_run(
                persistence_owner,
                run_id,
                status=current.status if current is not None else "failed",
                error_summary=f"运行失败：{type(exc).__name__}",
                updated_at=datetime.now(UTC),
            )
        raise


def cleanup_local_runs(output_root: str | Path, *, now: datetime | None = None) -> int:
    """Delete only expired, explicitly marked immediate children of the run root."""
    root = Path(output_root).resolve()
    if not root.is_dir():
        return 0
    current = now or datetime.now(UTC)
    deleted = 0
    for marker in root.glob("*/retention.json"):
        run_dir = marker.parent
        # Resolve and check the exact deletion target. Never follow a symlink
        # into an external archive or a frozen evaluation dataset.
        if run_dir.is_symlink() or run_dir.resolve().parent != root:
            continue
        try:
            record = json.loads(marker.read_text(encoding="utf-8"))
            if record["run_id"] != run_dir.name:
                continue
            deadline = datetime.fromisoformat(record["retention_deadline"])
            if deadline.tzinfo is None or deadline > current:
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        shutil.rmtree(run_dir)
        deleted += 1
    return deleted


async def resume_ui_request(
    run_id: str, decision: ApprovalDecision, *, output_root: str | Path = "streamlit-runs",
    owner_id: str, environ: Mapping[str, str] | None = None,
    run_store: RunStore | None = None, artifact_store: ArtifactStore | None = None,
) -> UiRunResult:
    run_id = require_run_identity(run_id)
    run_dir = Path(output_root) / run_id
    context = read_run_context(run_dir, run_id=run_id, owner_id=owner_id)
    if decision.approval_id != context.approval_id:
        raise HitlRuntimeError("approval_id does not match the pending request")
    if context.resolved_outcome and context.resolved_outcome != decision.outcome.value:
        raise HitlRuntimeError("approval was already resolved with a different outcome")
    options = context.options
    data_mode = "seed_urls"
    previous_path = run_dir / "result.json"
    if previous_path.is_file():
        data_mode = UiRunResult.model_validate_json(previous_path.read_text(encoding="utf-8")).data_mode
    request = UiRunRequest(
        **context.user.model_dump(), data_mode=data_mode,
        engine="hybrid", hitl=True, max_steps=options["max_steps"],
    )
    # Model keys and source transports are unnecessary after the saved interrupt.
    return await run_ui_request(
        request, output_root=output_root, owner_id=owner_id, environ=environ,
        run_store=run_store, artifact_store=artifact_store,
        _resume_run_id=run_id, _approval_decision=decision,
    )


def _safe_request_summary(request: UiRunRequest) -> dict[str, Any]:
    return {
        "keyword": request.keyword,
        "location": request.location,
        "target_count": request.target_count,
        "skills": list(request.skills),
        "data_mode": request.data_mode,
        "seed_url_count": len(request.seed_urls),
        "official_spec_count": len(request.official_specs or []),
        "uses_llm_extractor": bool(request.llm_extractor_provider),
        "uses_llm_match": bool(request.llm_match_provider),
        "engine": request.engine,
        "planner_strategy": request.planner_strategy,
        "hitl": request.hitl,
    }


def _run_artifact_path(run_dir: Path, reference: object) -> Path | None:
    if not isinstance(reference, str) or not reference.strip():
        return None
    candidate = Path(reference)
    path = candidate if candidate.is_absolute() else run_dir / candidate
    resolved = path.resolve(strict=False)
    if not resolved.is_file() or not resolved.is_relative_to(run_dir.resolve()):
        return None
    return resolved


def _bundle_source_archive(archive_root: Path, run_dir: Path) -> Path | None:
    archive_root = archive_root.resolve()
    if not archive_root.is_dir() or not archive_root.is_relative_to(run_dir.resolve()):
        return None
    destination = run_dir / "artifacts" / "source-archive.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(archive_root.rglob("*")):
                if not path.is_file() or path.name == ".archive.lock":
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(archive_root):
                    continue
                arcname = (Path(archive_root.name) / path.relative_to(archive_root)).as_posix()
                bundle.write(path, arcname)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _bundle_link_checks(run_dir: Path) -> Path | None:
    root = (run_dir / "link-checks").resolve()
    if not root.is_dir() or not root.is_relative_to(run_dir.resolve()):
        return None
    files = [path for path in sorted(root.glob("*.json"))
             if re.fullmatch(r"[0-9a-f]{64}\.json", path.name)
             and path.is_file() and path.resolve().is_relative_to(root)]
    if not files:
        return None
    destination = run_dir / "artifacts" / "link-checks.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in files:
                bundle.write(path, "link-checks/" + path.name)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _persist_artifacts(
    *,
    artifact_store: ArtifactStore,
    owner_id: str,
    run_id: str,
    artifacts: Mapping[str, Path],
    retention_deadline: datetime,
) -> dict[str, ArtifactRecord]:
    records: dict[str, ArtifactRecord] = {}
    for artifact_key, path in artifacts.items():
        if not path.is_file():
            continue
        display_name, mime = _artifact_metadata(artifact_key, path)
        records[artifact_key] = artifact_store.put_bytes(
            owner_id,
            run_id,
            artifact_key,
            path.read_bytes(),
            display_name=display_name,
            mime=mime,
            retention_deadline=retention_deadline,
        )
    return records


def _artifact_metadata(artifact_key: str, path: Path) -> tuple[str, str]:
    mime_by_key = {
        "json": "application/json",
        "report": "text/markdown",
        "dashboard": "text/html",
        "action_plan": "text/markdown",
        "source_manifest": "application/json",
        "source_archive": "application/zip",
        "link_checks": "application/zip",
    }
    return path.name, mime_by_key.get(artifact_key, "application/octet-stream")


def diagnostic_failure_count(failed_pages: int, diagnostics: list[dict[str, Any]]) -> int:
    return max(failed_pages, sum(item.get("kind") != "search_filter" for item in diagnostics))


def _terminal_run_status(
    *,
    failed_pages: int,
    diagnostics: list[dict[str, Any]],
) -> Literal["succeeded", "partial"]:
    return "partial" if diagnostic_failure_count(failed_pages, diagnostics) else "succeeded"


def _safe_terminal_summary(
    status: Literal["succeeded", "partial"],
    *,
    failed_pages: int,
    diagnostics: list[dict[str, Any]],
) -> str:
    if status == "succeeded":
        return ""
    failure_count = diagnostic_failure_count(failed_pages, diagnostics)
    return f"运行完成，但有 {failure_count} 个页面或诊断项失败"


def load_persisted_ui_result(
    run_store: RunStore,
    artifact_store: ArtifactStore,
    owner_id: str,
    run_id: str,
) -> UiRunResult | None:
    if run_store.get_run(owner_id, run_id) is None:
        return None
    try:
        _, content = artifact_store.get_artifact(owner_id, run_id, "json")
    except ArtifactNotFoundError:
        return None
    try:
        result = UiRunResult.model_validate_json(content)
    except ValueError:
        return None
    result.artifacts = {}
    result.artifact_records = {
        record.artifact_key: record
        for record in artifact_store.list_artifacts(owner_id, run_id)
    }
    return result


async def _build_browser(
    request: UiRunRequest,
    *,
    repository: JobRepository,
    page_loader: Callable[[str], Awaitable[Any]] | None,
    run_dir: Path | None = None,
    run_id: str | None = None,
) -> tuple[FakeBrowserClient | BrowserUseClient, list[str]]:
    from web_task_agent.agent_components import build_browser_components
    return await build_browser_components(
        request, repository=repository, page_loader=page_loader,
        aggregator_location=resolve_aggregator_location(request),
        candidate_limit=10,
        factories={"browser": BrowserUseClient, "fake": FakeBrowserClient, "http": HttpPageLoader,
                   "official": OfficialListSource, "aggregator": AggregatorRepoSource,
                   "official_api": OfficialApiContentFetcher, "loader": AggregatorPageLoader},
        archive_root=run_dir / "source-archive" / run_id if run_dir and run_id and request.engine == "hybrid" else None,
        run_id=run_id,
    )


def read_download_artifact(
    result: UiRunResult,
    artifact_key: str,
    *,
    artifact_store: ArtifactStore | None = None,
    owner_id: str | None = None,
) -> tuple[str, bytes]:
    if artifact_store is not None and owner_id is not None:
        if artifact_key in result.artifact_records:
            record, content = artifact_store.get_artifact(
                owner_id,
                result.run_id,
                artifact_key,
            )
            return record.display_name, content
    path = result.artifacts.get(artifact_key)
    if path is None or not path.is_file():
        raise UiRequestError("该产物不可下载。")
    return path.name, path.read_bytes()
