from __future__ import annotations

import asyncio
import hashlib
import html
import os
import re
import tempfile
from collections.abc import Callable, Mapping, MutableMapping
from hmac import compare_digest
from pathlib import Path
from typing import Any

from web_task_agent.agent_approval import ApprovalDecision
from web_task_agent.artifact_store import (
    ArtifactStore,
    FileArtifactStore,
    S3ArtifactStore,
)
from web_task_agent.models import RunMetrics
from web_task_agent.provider_health import check_deepseek_capabilities
from web_task_agent.run_store import FileRunStore, PostgresRunStore, RunRecord, RunStore
from web_task_agent.source_catalog import (
    SOURCE_LIMITATIONS,
    VERIFIED_SOURCE_DATE,
    VERIFIED_SOURCE_SCOPE,
    VERIFIED_SOURCES,
)
from web_task_agent.streamlit_runner import (
    DEFAULT_AGGREGATOR_URL,
    PROVIDER_API_KEY_ENV,
    UiRequestError,
    UiRunRequest,
    UiRunResult,
    cleanup_local_runs,
    decode_resume_upload,
    diagnostic_failure_count,
    load_persisted_ui_result,
    parse_seed_urls,
    parse_skills,
    read_download_artifact,
    resume_ui_request,
    run_ui_request,
    sync_provider_secrets,
    validate_ui_request,
)

PRIORITY_LABELS = {"high": "高", "medium": "中", "low": "低"}
ARTIFACT_SPECS = {
    "json": ("下载 JSON", "application/json"),
    "report": ("下载 Markdown 报告", "text/markdown"),
    "dashboard": ("下载 HTML Dashboard", "text/html"),
    "action_plan": ("下载行动计划", "text/markdown"),
    "link_checks": ("下载链接核验证据", "application/zip"),
}

MODE_HINTS = {
    "demo": "内置夹具数据：适合快速查看完整链路，不访问公网。",
    "official": "实时连接官方招聘 API：默认使用小米实习来源，逐项独立核验交付链接。",
    "aggregator": "聚合岗位：从上传文件或 jobs.json 读取岗位，再执行统一验证和匹配。",
    "seed_urls": "指定岗位 URL：适合验证单个或一组真实岗位页面。",
    "web_search": "DeepSeek Search 优先搜索已核验大厂的官方单岗详情。岗位须以开发为主，且通过投递入口核验。",
}

MODE_LABELS = {
    "实时岗位（官方 API）": "official", "网页搜索": "web_search", "指定岗位 URL": "seed_urls",
    "聚合岗位（上传/URL）": "aggregator", "内置 Demo": "demo",
}

ARTIFACT_DESCRIPTIONS = {
    "json": "机器可读的完整运行状态、岗位、匹配和诊断。",
    "report": "本次运行的可复核结果，适合阅读和分享。",
    "dashboard": "可筛选岗位、匹配分和执行轨迹的 HTML 页面。",
    "action_plan": "根据岗位缺口生成的投递和技能补强计划。",
    "link_checks": "公开岗位网页正文、实际地址和投递入口的核验证据。",
}

DEFAULT_PERSISTENCE_ROOT = "streamlit-persistence"
RUN_STATUS_LABELS = {
    "queued": "排队中",
    "running": "运行中",
    "succeeded": "已完成",
    "partial": "部分完成",
    "failed": "失败",
    "awaiting_approval": "等待确认",
    "rejected": "已拒绝",
}

RESUME_PREVIEW_TEXT_KEY = "resume-preview-text"
RESUME_UPLOAD_FINGERPRINT_KEY = "resume-upload-fingerprint"
RESUME_UPLOAD_ERROR_KEY = "resume-upload-error"
RESUME_UPLOAD_BLOCKED_KEY = "resume-upload-blocked"

DEFAULT_TARGET_COUNT = 3
DEFAULT_OFFICIAL_SPECS = ("xiaomi",)
# Retired providers must stay hidden even if a legacy deployment retains their key.
UI_LLM_PROVIDERS = ("deepseek",)

TERMINAL_REASON_LABELS = {
    "budget_exhausted": "本轮检查步数已用完，已交付确认的部分岗位",
    "step_budget_exhausted": "本轮检查步数已用完，已交付确认的部分岗位",
    "steps_exhausted": "本轮检查步数已用完，已交付确认的部分岗位",
    "deadline_exceeded": "达到本次总时限，已停止处理",
    "deadline": "达到本次总时限，已停止处理",
    "deadline_reached": "达到本次总时限，已停止处理",
    "timeout": "达到本次总时限，已停止处理",
    "candidates_exhausted": "本轮候选已全部检查完，没有更多可交付岗位",
    "candidate_pool_exhausted": "本轮候选已全部检查完，没有更多可交付岗位",
    "no_candidates": "来源没有返回符合条件的候选岗位",
    "search_error": "搜索来源暂时不可用，请稍后重试",
    "source_error": "搜索来源暂时不可用，请稍后重试",
    "target_reached": "已达到目标岗位数",
    "target_count_reached": "已达到目标岗位数",
    "rejected": "已拒绝保存本次结果",
    "user_rejected": "已拒绝保存本次结果",
}

DIAGNOSTIC_CATEGORY_LABELS = {
    "budget_exhausted": "本轮检查步数已用完",
    "step_budget_exhausted": "本轮检查步数已用完",
    "deadline_exceeded": "达到本次总时限",
    "deadline": "达到本次总时限",
    "timeout": "请求超时",
}


def configured_app_passcode(
    *,
    environ: Mapping[str, str] | None = None,
    secrets: Mapping[str, Any] | None = None,
) -> str:
    current_environ = os.environ if environ is None else environ
    environment_value = str(current_environ.get("APP_PASSCODE", "")).strip()
    if environment_value:
        return environment_value
    secret_value = (secrets or {}).get("APP_PASSCODE", "")
    return str(secret_value).strip()


def app_access_granted(configured_passcode: str, supplied_passcode: str) -> bool:
    if not configured_passcode:
        return True
    return compare_digest(configured_passcode, supplied_passcode)


def recent_run_rows(records: list[RunRecord]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        rows.append(
            {
                "运行 ID": record.run_id,
                "状态": RUN_STATUS_LABELS.get(record.status, record.status),
                "数据模式": record.data_mode,
                "有效岗位": int(record.metrics.get("valid_jobs", 0) or 0),
                "创建时间": record.created_at.strftime("%Y-%m-%d %H:%M"),
            }
        )
    return rows


def build_file_persistence(
    root: str | Path = DEFAULT_PERSISTENCE_ROOT,
) -> tuple[FileRunStore, FileArtifactStore]:
    base = Path(root)
    return FileRunStore(base / "runs"), FileArtifactStore(base / "artifacts")


def build_configured_persistence(
    root: str | Path = DEFAULT_PERSISTENCE_ROOT,
    *,
    environ: Mapping[str, str] | None = None,
    postgres_factory: Callable[[str], RunStore] | None = None,
    s3_factory: Callable[..., ArtifactStore] | None = None,
) -> tuple[RunStore, ArtifactStore]:
    current_environ = os.environ if environ is None else environ
    cloud_names = (
        "DATABASE_URL",
        "OBJECT_STORAGE_ENDPOINT",
        "OBJECT_STORAGE_BUCKET",
        "OBJECT_STORAGE_ACCESS_KEY",
        "OBJECT_STORAGE_SECRET_KEY",
    )
    configured = {
        name for name in cloud_names if str(current_environ.get(name, "")).strip()
    }
    if not configured:
        return build_file_persistence(root)
    missing = [name for name in cloud_names if name not in configured]
    if missing:
        raise ValueError(f"云端持久化配置不完整：{', '.join(missing)}")

    database_url = str(current_environ["DATABASE_URL"]).strip()
    run_store = (
        postgres_factory(database_url)
        if postgres_factory is not None
        else PostgresRunStore(database_url)
    )
    initialize = getattr(run_store, "initialize", None)
    if callable(initialize):
        initialize()
    artifact_store = (
        s3_factory(
            bucket=str(current_environ["OBJECT_STORAGE_BUCKET"]).strip(),
            endpoint_url=str(current_environ["OBJECT_STORAGE_ENDPOINT"]).strip(),
            access_key=str(current_environ["OBJECT_STORAGE_ACCESS_KEY"]).strip(),
            secret_key=str(current_environ["OBJECT_STORAGE_SECRET_KEY"]).strip(),
        )
        if s3_factory is not None
        else S3ArtifactStore(
            bucket=str(current_environ["OBJECT_STORAGE_BUCKET"]).strip(),
            endpoint_url=str(current_environ["OBJECT_STORAGE_ENDPOINT"]).strip(),
            access_key=str(current_environ["OBJECT_STORAGE_ACCESS_KEY"]).strip(),
            secret_key=str(current_environ["OBJECT_STORAGE_SECRET_KEY"]).strip(),
        )
    )
    return run_store, artifact_store


def _streamlit_secrets(st: Any) -> dict[str, Any]:
    try:
        return dict(st.secrets)
    except Exception:
        return {}


def mode_hint(data_mode: str) -> str:
    return MODE_HINTS.get(data_mode, "选择一种数据来源开始运行。")


def humanize_terminal_reason(reason: str) -> str:
    normalized = str(reason or "").strip()
    if not normalized:
        return ""
    key = normalized.casefold()
    if key in TERMINAL_REASON_LABELS:
        return TERMINAL_REASON_LABELS[key]
    if "budget" in key and ("exhaust" in key or "step" in key):
        return TERMINAL_REASON_LABELS["budget_exhausted"]
    if "deadline" in key or "timeout" in key or "timed out" in key:
        return TERMINAL_REASON_LABELS["deadline_exceeded"]
    if "candidate" in key and "exhaust" in key:
        return TERMINAL_REASON_LABELS["candidates_exhausted"]
    if "target" in key and ("reach" in key or "count" in key):
        return TERMINAL_REASON_LABELS["target_reached"]
    return f"运行提前结束（{normalized}）"


def terminal_reason_notice(result: UiRunResult) -> str:
    readable = humanize_terminal_reason(result.terminal_reason)
    if readable:
        return readable
    status_key = str(result.terminal_status or "").strip().casefold()
    if status_key in TERMINAL_REASON_LABELS:
        return TERMINAL_REASON_LABELS[status_key]
    for item in result.diagnostics:
        error = str(item.get("error", ""))
        category = error.partition(":")[0].strip().casefold()
        readable = humanize_terminal_reason(category)
        if readable and category in TERMINAL_REASON_LABELS:
            return readable
    return ""


def saved_status_text(result: UiRunResult) -> str:
    pending_count = len(_pending_jobs(result))
    delivered_count = len(_delivered_jobs(result))
    if result.run_status == "awaiting_approval":
        return "本次结果尚未保存，等待确认后写入岗位库。"
    if result.run_status == "rejected":
        return "本次结果未保存到岗位库。"
    if "saved" not in getattr(result, "model_fields_set", set()):
        return "本次结果未记录保存状态；仍可查看和下载结果文件。"
    if bool(getattr(result, "saved", False)):
        if pending_count:
            return (
                f"已交付的 {delivered_count} 个岗位已保存到岗位库；"
                f"{pending_count} 个未交付候选未保存。"
            )
        return "本次结果已保存到岗位库。"
    if pending_count:
        return "本次没有已交付岗位；未交付候选未保存到岗位库。"
    return "本次结果未保存到岗位库；仍可查看和下载结果文件。"


def run_status(result: UiRunResult | None) -> tuple[str, str, str]:
    if result is None:
        return "待运行", "配置任务后开始一次岗位扫描", "ready"
    if result.run_status == "awaiting_approval":
        return "等待确认", f"{result.metrics.valid_jobs} 个岗位等待确认保存", "warning"
    if result.run_status == "rejected":
        return "已拒绝", "本次结果未保存到岗位库", "warning"
    if result.data_mode != "demo" and any(not _link_receipt(job) for job in result.jobs):
        return "历史结果未核验", "旧流程结果缺少链接核验记录，请重新运行确认页面有效性", "warning"
    if result.run_status in {"partial", "failed"}:
        readable_reason = humanize_terminal_reason(result.terminal_reason) or "结果未完整"
        return (
            "部分完成" if result.run_status == "partial" else "失败",
            f"{result.metrics.valid_jobs} 个有效岗位 · {readable_reason}",
            "warning",
        )
    pending_count = len(_pending_jobs(result))
    delivered_count = len(_delivered_jobs(result))
    if pending_count and delivered_count == 0:
        return "等待核验", f"{pending_count} 个候选未交付 · 0 个交付岗位", "warning"
    failure_count = diagnostic_failure_count(result.metrics.failed_pages, result.diagnostics)
    if failure_count:
        return (
            "已完成",
            f"{result.metrics.valid_jobs} 个有效岗位 · {failure_count} 个页面失败",
            "warning",
        )
    if pending_count:
        return (
            "已完成",
            f"{delivered_count} 个已交付岗位 · {pending_count} 个未交付候选",
            "warning",
        )
    return "已完成", f"{result.metrics.valid_jobs} 个有效岗位 · 页面访问正常", "success"


def resume_upload_fingerprint(uploaded: Any | None) -> str | None:
    if uploaded is None:
        return None
    content = uploaded.getvalue()
    name = str(getattr(uploaded, "name", ""))
    digest = hashlib.sha256(content).hexdigest()
    return f"{name}:{len(content)}:{digest}"


def sync_resume_preview(
    state: MutableMapping[str, Any],
    uploaded: Any | None,
) -> None:
    """Decode an upload once per file and keep user edits until the file changes."""
    fingerprint = resume_upload_fingerprint(uploaded)
    if state.get(RESUME_UPLOAD_FINGERPRINT_KEY) == fingerprint:
        return
    state[RESUME_UPLOAD_FINGERPRINT_KEY] = fingerprint
    state[RESUME_PREVIEW_TEXT_KEY] = ""
    state[RESUME_UPLOAD_ERROR_KEY] = ""
    state[RESUME_UPLOAD_BLOCKED_KEY] = False
    if uploaded is None:
        return
    try:
        text = decode_resume_upload(
            uploaded.getvalue(),
            filename=str(getattr(uploaded, "name", "")),
        )
    except UiRequestError as exc:
        state[RESUME_UPLOAD_ERROR_KEY] = str(exc)
        state[RESUME_UPLOAD_BLOCKED_KEY] = True
        return
    except Exception:  # noqa: BLE001
        state[RESUME_UPLOAD_ERROR_KEY] = (
            "无法读取简历文件，请确认文件未损坏，或移除文件后直接粘贴文字。"
        )
        state[RESUME_UPLOAD_BLOCKED_KEY] = True
        return
    state[RESUME_PREVIEW_TEXT_KEY] = text


def resume_run_blocker(state: Mapping[str, Any]) -> str:
    if not state.get(RESUME_UPLOAD_BLOCKED_KEY):
        return ""
    return str(state.get(RESUME_UPLOAD_ERROR_KEY) or "简历文件无法读取，请更换或移除文件。")


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _progress_values(result: UiRunResult) -> dict[str, int]:
    raw = getattr(result, "progress", None)
    progress: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    limit = _as_int(progress.get("limit"), 10)
    discovered = _as_int(
        progress.get("discovered"),
        _as_int(getattr(result, "unique_candidates", 0), 0),
    )
    checked = _as_int(progress.get("checked"), result.metrics.pages_visited)
    selected = _as_int(progress.get("selected"), min(discovered, limit))
    verified = _as_int(progress.get("verified"), len(result.jobs))
    delivered = _as_int(progress.get("delivered"), result.metrics.valid_jobs)
    if "saved" in result.model_fields_set and not result.saved:
        delivered = 0
    unexamined = _as_int(progress.get("unexamined"), max(0, discovered - checked))
    return {
        "discovered": discovered,
        "selected": selected,
        "checked": checked,
        "verified": verified,
        "delivered": delivered,
        "unexamined": unexamined,
        "limit": limit,
    }


def progress_summary_text(result: UiRunResult) -> str:
    values = _progress_values(result)
    return (
        f"本轮：发现 {values['discovered']} 条候选 · 选定 {values['selected']} 条 · "
        f"已检查 {values['checked']} 条 · 验证通过 {values['verified']} 条 · "
        f"已交付 {values['delivered']} 条 · 未检查 {values['unexamined']} 条"
        f"（检查上限 {values['limit']}）"
    )


def _filter_reason_summary(result: UiRunResult) -> str:
    counts = {"search": 0, "verification": 0, "source": 0, "page": 0}
    for item in result.diagnostics:
        error = str(item.get("error", ""))
        kind = str(item.get("kind", ""))
        category = error.partition(":")[0].strip()
        if kind == "search_filter" or category == "search_filtered":
            counts["search"] += 1
        elif kind == "verification" or category == "verification_filtered":
            counts["verification"] += 1
        elif category.startswith("source_") or kind == "source":
            counts["source"] += 1
        else:
            counts["page"] += 1
    labels = (
        ("search", "关键词或地点过滤"),
        ("verification", "正文验证过滤"),
        ("source", "来源请求失败"),
        ("page", "页面读取失败"),
    )
    parts = [f"{label} {counts[key]}" for key, label in labels if counts[key]]
    return "；".join(parts)


def empty_result_explanation(result: UiRunResult) -> str:
    values = _progress_values(result)
    parts = [
        "本轮没有可交付的已验证岗位。",
        (
            f"发现 {values['discovered']} 条候选，选定 {values['selected']} 条，"
            f"已检查 {values['checked']} 条，验证通过 {values['verified']} 条。"
        ),
    ]
    if values["unexamined"]:
        parts.append(
            f"还有 {values['unexamined']} 条候选未检查（本轮检查上限 {values['limit']}）。"
        )
    else:
        parts.append("本轮候选已检查完。")
    reasons = _filter_reason_summary(result)
    parts.append(f"过滤原因：{reasons}。" if reasons else "没有记录到具体过滤原因，请查看失败与诊断。")
    notice = terminal_reason_notice(result)
    if notice:
        parts.append(notice + "。")
    return " ".join(parts)


def _escaped(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _score_text(score: float) -> str:
    normalized = min(max(float(score), 0.0), 1.0)
    return f"{normalized:.0%}（{normalized:.2f}）"


def internship_evidence_text(job: Any) -> str:
    source = str(getattr(job, "internship_evidence_source", "") or "").strip()
    value = str(getattr(job, "internship_evidence_value", "") or "").strip()
    if source.startswith("xiaomi:list:type="):
        value = value or source.partition("=")[2]
        source = "xiaomi:list:type"
    elif source.startswith("xiaomi:detail:recruit_type.id="):
        value = value or source.partition("=")[2]
        source = "xiaomi:detail:recruit_type.id"
    if source == "xiaomi:list:type":
        label = "官方实习" if value == "3" else "官方类型字段"
        return f"{label}（小米列表 type={value or '未知'}）"
    if source == "xiaomi:detail:recruit_type.id":
        label = "官方实习" if value == "202" else "官方类型字段"
        return f"{label}（小米详情 recruit_type.id={value or '未知'}）"
    if source or value:
        return f"{source + '=' if source else ''}{value or '未知'}"
    title = str(getattr(job, "title", "") or "").casefold()
    if "实习" in title:
        return "标题含“实习”"
    if "intern" in title:
        return "标题含“intern”"
    return "未提供"


BODY_SOURCE_LABELS = {
    "api-verified": "官方详情 API（正文来源）",
    "detail-probed": "详情探测（正文来源）",
    "list-attested": "列表数据（正文来源，未独立核验）",
    "http-fetched": "HTTP 抓取（正文来源）",
    "historical": "历史正文（当前可用性未知）",
}

LINK_RECEIPT_LABELS = {
    "verified": "✓ 已核验",
    "pending": "待核验",
    "rejected": "未通过",
}


def _link_receipt(job: Any) -> dict[str, Any]:
    value = getattr(job, "link_verification", {})
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dict(dumped)
    return {}


def body_source_text(job: Any) -> str:
    verification = str(getattr(job, "url_verification", "") or "")
    return BODY_SOURCE_LABELS.get(verification, "来源未标注")


def link_verification_text(job: Any) -> str:
    receipt = _link_receipt(job)
    if not receipt:
        return "未核验（无核验记录）"
    status = str(receipt.get("status") or "").strip().casefold()
    if status in LINK_RECEIPT_LABELS:
        return LINK_RECEIPT_LABELS[status]
    return "核验状态未知" if not status else f"核验状态未知（{status}）"


def _receipt_value(receipt: Mapping[str, Any], key: str) -> str:
    value = receipt.get(key)
    return str(value).strip() if value is not None and str(value).strip() else "—"


def _pending_jobs(result: UiRunResult) -> list[Any]:
    return list(getattr(result, "pending_jobs", []) or [])


def _delivered_jobs(result: UiRunResult) -> list[Any]:
    pending_urls = {
        str(getattr(job, "url", "") or "") for job in _pending_jobs(result)
    }
    return [
        job for job in result.jobs if str(getattr(job, "url", "") or "") not in pending_urls
    ]


def _pending_reason(job: Any, result: UiRunResult) -> str:
    receipt = _link_receipt(job)
    reason = str(receipt.get("reason") or "").strip()
    if reason:
        return reason
    url = str(getattr(job, "url", "") or "")
    for item in result.diagnostics:
        if str(item.get("url", "")) != url:
            continue
        detail = str(item.get("error", "")).partition(":")[2].strip()
        if detail:
            return detail
    return "未提供核验原因"


def pending_job_rows(result: UiRunResult) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for job in _pending_jobs(result):
        receipt = _link_receipt(job)
        rows.append(
            {
                "岗位": str(getattr(job, "title", "") or ""),
                "公司": str(getattr(job, "company", "") or ""),
                "地点": str(getattr(job, "location", "") or ""),
                "状态": link_verification_text(job),
                "未交付原因": _pending_reason(job, result),
                "检查时间": _receipt_value(receipt, "checked_at"),
                "核验方式": _receipt_value(receipt, "method"),
                "请求URL": _receipt_value(receipt, "requested_url")
                if receipt
                else str(getattr(job, "url", "") or "—"),
                "最终URL": _receipt_value(receipt, "final_url"),
            }
        )
    return rows


def pending_jobs_html(result: UiRunResult) -> str:
    pending = _pending_jobs(result)
    if not pending:
        return ""
    cards: list[str] = []
    for job, row in zip(pending, pending_job_rows(result), strict=True):
        cards.append(
            f'<article class="ui-pending-card">'
            f'<div class="ui-pending-head"><div><strong>{_escaped(row["岗位"])}</strong>'
            f'<span>{_escaped(row["公司"])} · {_escaped(row["地点"])}</span></div>'
            f'<span class="ui-pending-status">{_escaped(row["状态"])}</span></div>'
            f'<dl class="ui-pending-facts">'
            f'<div><dt>未交付原因</dt><dd>{_escaped(row["未交付原因"])}</dd></div>'
            f'<div><dt>检查时间</dt><dd>{_escaped(row["检查时间"])}</dd></div>'
            f'<div><dt>核验方式</dt><dd>{_escaped(row["核验方式"])}</dd></div>'
            f'<div><dt>最终URL</dt><dd>{_escaped(row["最终URL"])}</dd></div>'
            f'</dl><a class="ui-job-link" href="{_escaped(getattr(job, "url", ""))}" '
            f'target="_blank" rel="noopener noreferrer">查看候选来源 ↗</a></article>'
        )
    delivered = len(_delivered_jobs(result))
    return (
        '<section class="ui-pending"><div class="ui-pending-title">'
        f'<h3>未交付候选 · {len(pending)}</h3><span>{delivered} 个交付</span></div>'
        '<p>下列候选未通过或尚未完成链接核验，不进入主匹配表，也不计入已保存岗位。</p>'
        f'<div class="ui-pending-grid">{"".join(cards)}</div></section>'
    )


def match_basis_text(match: Any | None) -> str:
    if match is None:
        return "未生成匹配说明（岗位仍保留展示）"
    reason = str(getattr(match, "reason", "") or "").strip()
    if reason:
        return reason
    matched = list(getattr(match, "matched_skills", []) or [])
    missing = list(getattr(match, "missing_skills", []) or [])
    parts: list[str] = []
    if matched:
        parts.append(f"命中 {len(matched)} 项技能（{'、'.join(matched)}）")
    else:
        parts.append("未命中已登记技能")
    if missing:
        parts.append(f"缺少 {'、'.join(missing)}")
    else:
        parts.append("没有登记缺失技能")
    parts.append(f"匹配分 {_score_text(match.score)}")
    return "；".join(parts) + "。"


def job_cards_html(result: UiRunResult) -> str:
    matches = {match.job_id: match for match in result.matches}
    cards: list[str] = []
    for job in _delivered_jobs(result):
        receipt = _link_receipt(job)
        match = matches.get(job.url)
        score = float(match.score) if match is not None else 0.0
        matched = "、".join(match.matched_skills) if match and match.matched_skills else "无"
        missing = "、".join(match.missing_skills) if match and match.missing_skills else "无"
        tone = " ui-job-card-low" if score < 0.5 else ""
        cards.append(
            f'<article class="ui-job-card{tone}">'
            f'<div class="ui-job-card-head"><div>'
            f'<span class="ui-job-source">{_escaped(job.source)}</span>'
            f"<h3>{_escaped(job.title)}</h3></div>"
            f'<span class="ui-job-score">匹配 {_score_text(score)}</span></div>'
            f'<p class="ui-job-meta">公司 {_escaped(job.company)} · 城市 {_escaped(job.location)}</p>'
            f'<dl class="ui-job-facts">'
            f"<div><dt>实习依据</dt><dd>{_escaped(internship_evidence_text(job))}</dd></div>"
            f"<div><dt>正文来源</dt><dd>{_escaped(body_source_text(job))}</dd></div>"
            f"<div><dt>链接核验</dt><dd>{_escaped(link_verification_text(job))}</dd></div>"
            f"<div><dt>核验时间</dt><dd>{_escaped(_receipt_value(receipt, 'checked_at'))}</dd></div>"
            f"<div><dt>核验方式</dt><dd>{_escaped(_receipt_value(receipt, 'method'))}</dd></div>"
            f"<div><dt>最终URL</dt><dd>{_escaped(_receipt_value(receipt, 'final_url'))}</dd></div>"
            f"<div><dt>匹配技能</dt><dd>{_escaped(matched)}</dd></div>"
            f"<div><dt>缺失技能</dt><dd>{_escaped(missing)}</dd></div>"
            f"<div><dt>评分依据</dt><dd>{_escaped(match_basis_text(match))}</dd></div>"
            f"</dl>"
            f'<a class="ui-job-link" href="{_escaped(job.url)}" target="_blank" rel="noopener noreferrer">打开原文 ↗</a>'
            f"</article>"
        )
    if not cards:
        return ""
    return '<div class="ui-job-card-grid">' + "".join(cards) + "</div>"


def app_styles() -> str:
    return """
<style>
:root {
  --ui-ink: #16211d;
  --ui-muted: #66736d;
  --ui-border: #dce5df;
  --ui-surface: #ffffff;
  --ui-canvas: #f4f7f5;
  --ui-green: #18794e;
  --ui-green-soft: #e6f4eb;
  --ui-warning: #a46114;
  --ui-warning-soft: #fff5e5;
  --ui-shadow: 0 10px 30px rgba(20, 47, 34, .06);
}
.stApp { background: var(--ui-canvas); color: var(--ui-ink); }
[data-testid="stHeader"] { background: transparent; }
.block-container { max-width: 1440px; padding-top: 2.4rem; padding-bottom: 4rem; }
[data-testid="stSidebar"] { background: #f8faf9; border-right: 1px solid #d5e0da; }
[data-testid="stSidebar"] > div:first-child { padding: 1.4rem 1rem 2rem; }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p,
[data-testid="stSidebar"] .ui-sidebar-caption { color: #4c6057 !important; }
[data-testid="stSidebar"] [data-testid="stExpander"] { background: #fff; border-color: #d5e0da; }
.ui-brand { display:flex; align-items:flex-start; justify-content:space-between; gap:1.5rem; margin-bottom:2rem; }
.ui-brand-mark { display:flex; align-items:center; gap:.7rem; }
.ui-brand-dot { width:13px; height:13px; border-radius:4px; background:var(--ui-green); box-shadow:0 0 0 5px #d8efe1; }
.ui-eyebrow { color:var(--ui-green); font-size:.72rem; font-weight:800; letter-spacing:.12em; text-transform:uppercase; margin-bottom:.35rem; }
.ui-brand h1 { color:var(--ui-ink); font-size:2rem; line-height:1.1; letter-spacing:-.04em; margin:0; }
.ui-brand p { color:var(--ui-muted); margin:.45rem 0 0; font-size:.95rem; }
.ui-status { display:flex; align-items:center; gap:.55rem; white-space:nowrap; border:1px solid var(--ui-border); border-radius:999px; background:var(--ui-surface); padding:.5rem .8rem; color:var(--ui-muted); font-size:.82rem; box-shadow:var(--ui-shadow); }
.ui-status strong { color:var(--ui-ink); }
.ui-status-dot { width:8px; height:8px; border-radius:50%; background:#9ca8a1; }
.ui-status-success .ui-status-dot { background:var(--ui-green); }
.ui-status-warning .ui-status-dot { background:#d68b2b; }
.ui-sidebar-brand { padding:.3rem .3rem 1.3rem; border-bottom:1px solid #d5e0da; margin-bottom:1.2rem; }
.ui-sidebar-brand strong { display:block; color:#163b2b; font-size:1.15rem; letter-spacing:-.02em; }
.ui-sidebar-brand span { color:#4e6559; font-size:.82rem; }
.ui-sidebar-label { color:#426453; font-size:.75rem; font-weight:750; letter-spacing:.06em; margin:1.2rem .2rem .55rem; }
.ui-mode-hint { border:1px solid #c7d9ce; background:#eaf3ed; border-radius:10px; padding:.7rem .75rem; color:#274b38; font-size:.82rem; line-height:1.55; margin:.55rem 0 1rem; }
[data-testid="stForm"] { border:1px solid var(--ui-border); border-radius:16px; background:var(--ui-surface); box-shadow:var(--ui-shadow); padding:1.35rem 1.45rem 1.5rem; }
.ui-form-title { color:var(--ui-ink); font-size:1.05rem; font-weight:750; margin-bottom:.15rem; }
.ui-form-caption { color:var(--ui-muted); font-size:.84rem; margin-bottom:1.1rem; }
.ui-section-label { color:var(--ui-green); font-size:.7rem; font-weight:800; letter-spacing:.1em; text-transform:uppercase; margin:1rem 0 .45rem; }
.ui-result-header { display:flex; align-items:flex-end; justify-content:space-between; gap:1rem; margin:2rem 0 .9rem; }
.ui-result-header h2 { color:var(--ui-ink); margin:0; letter-spacing:-.03em; }
.ui-result-header p { color:var(--ui-muted); margin:.25rem 0 0; font-size:.86rem; }
.ui-result-chip { border-radius:999px; background:var(--ui-green-soft); color:var(--ui-green); padding:.35rem .65rem; font-size:.75rem; font-weight:700; }
.ui-metric-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:.8rem; margin:0 0 1.1rem; }
.ui-metric { border:1px solid var(--ui-border); border-radius:12px; background:var(--ui-surface); padding:.9rem 1rem; box-shadow:var(--ui-shadow); }
.ui-metric span { display:block; color:var(--ui-muted); font-size:.78rem; margin-bottom:.35rem; }
.ui-metric strong { display:block; color:var(--ui-ink); font-size:1.55rem; letter-spacing:-.04em; }
.ui-metric-success { border-top:3px solid var(--ui-green); }
.ui-metric-warning { border-top:3px solid #d68b2b; }
.ui-metric-neutral { border-top:3px solid #9ca8a1; }
.ui-empty { border:1px dashed var(--ui-border); border-radius:12px; background:#fbfcfb; padding:1.4rem; color:var(--ui-muted); text-align:center; }
.ui-download-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:.8rem; margin-bottom:1rem; }
.ui-download-card { border:1px solid var(--ui-border); border-radius:12px; background:var(--ui-surface); padding:1rem; min-height:116px; }
.ui-download-card strong { display:block; color:var(--ui-ink); margin-bottom:.3rem; }
.ui-download-card span { display:block; color:var(--ui-muted); font-size:.8rem; line-height:1.45; }
.ui-download-card code { display:block; color:#7a877e; font-size:.7rem; margin-top:.55rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.ui-job-card-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:.9rem; margin:.3rem 0 1.1rem; }
.ui-job-card { border:1px solid var(--ui-border); border-left:4px solid var(--ui-green); border-radius:14px; background:var(--ui-surface); padding:1.05rem 1.1rem; box-shadow:var(--ui-shadow); }
.ui-job-card-low { border-left-color:#d68b2b; }
.ui-job-card-head { display:flex; align-items:flex-start; justify-content:space-between; gap:.8rem; }
.ui-job-card-head h3 { color:var(--ui-ink); font-size:1.02rem; line-height:1.35; margin:.18rem 0 0; letter-spacing:-.02em; }
.ui-job-source { color:var(--ui-green); font-size:.67rem; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.ui-job-score { flex:none; border-radius:999px; background:var(--ui-green-soft); color:var(--ui-green); padding:.28rem .55rem; font-size:.73rem; font-weight:750; }
.ui-job-card-low .ui-job-score { background:var(--ui-warning-soft); color:var(--ui-warning); }
.ui-job-meta { color:var(--ui-muted); font-size:.82rem; margin:.55rem 0 .7rem; }
.ui-job-facts { display:grid; gap:.42rem; margin:0 0 .8rem; }
.ui-job-facts div { display:grid; grid-template-columns:5.2rem 1fr; gap:.55rem; }
.ui-job-facts dt { color:var(--ui-muted); font-size:.72rem; font-weight:700; }
.ui-job-facts dd { color:var(--ui-ink); font-size:.8rem; line-height:1.45; margin:0; }
.ui-job-link { display:inline-flex; color:var(--ui-green); font-size:.8rem; font-weight:750; text-decoration:none; }
.ui-job-link:hover { text-decoration:underline; }
.ui-pending { border:1px solid #edd2a6; border-radius:14px; background:#fffaf0; padding:1rem 1.1rem; margin:.3rem 0 1.1rem; }
.ui-pending-title { display:flex; align-items:center; justify-content:space-between; gap:.8rem; border-bottom:1px solid #f1dfc2; padding-bottom:.65rem; }
.ui-pending-title h3 { color:#7a4a0f; font-size:1rem; margin:0; }
.ui-pending-title span { color:#8b6330; font-size:.75rem; font-weight:750; }
.ui-pending > p { color:#765a35; font-size:.8rem; line-height:1.5; margin:.7rem 0 .8rem; }
.ui-pending-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:.7rem; }
.ui-pending-card { border:1px solid #f1dfc2; border-left:4px solid #c68225; border-radius:10px; background:#fffdf8; padding:.8rem .85rem; }
.ui-pending-head { display:flex; align-items:flex-start; justify-content:space-between; gap:.7rem; }
.ui-pending-head strong { display:block; color:var(--ui-ink); font-size:.88rem; }
.ui-pending-head span { display:block; color:var(--ui-muted); font-size:.72rem; margin-top:.16rem; }
.ui-pending-status { flex:none; border-radius:999px; background:var(--ui-warning-soft); color:var(--ui-warning); padding:.2rem .45rem; font-size:.68rem; font-weight:750; }
.ui-pending-facts { display:grid; gap:.3rem; margin:.7rem 0; }
.ui-pending-facts div { display:grid; grid-template-columns:5.2rem 1fr; gap:.45rem; }
.ui-pending-facts dt { color:#8b7355; font-size:.68rem; font-weight:700; }
.ui-pending-facts dd { color:#5e4b32; font-size:.75rem; line-height:1.4; margin:0; overflow-wrap:anywhere; }
.ui-progress-note { color:var(--ui-muted); font-size:.8rem; line-height:1.5; margin:-.65rem 0 1rem; }
@media (max-width: 760px) {
  .block-container { padding:1.35rem .9rem 3rem; }
  .ui-brand { display:block; margin-bottom:1.35rem; }
  .ui-status { display:inline-flex; margin-top:1rem; }
  .ui-brand h1 { font-size:1.65rem; }
  .ui-metric-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .ui-download-grid { grid-template-columns:1fr; }
  .ui-job-card-grid { grid-template-columns:1fr; }
  .ui-pending-grid { grid-template-columns:1fr; }
  [data-testid="stForm"] { padding:.95rem; }
}
</style>
"""


def brand_header_html(result: UiRunResult | None) -> str:
    status, detail, tone = run_status(result)
    return f"""
<div class="ui-brand">
  <div>
    <div class="ui-eyebrow">AI career workspace</div>
    <div class="ui-brand-mark"><span class="ui-brand-dot"></span><h1>岗位 Agent 运行台</h1></div>
    <p>从官方岗位发现到可解释匹配，把一次求职搜索变成一份可复核结果。</p>
  </div>
  <div class="ui-status ui-status-{tone}"><span class="ui-status-dot"></span><span><strong>{status}</strong> · {detail}</span></div>
</div>
"""


def artifact_cards_html(result: UiRunResult) -> str:
    cards = []
    labels = {
        "json": "JSON 结果",
        "report": "Markdown 报告",
        "dashboard": "HTML Dashboard",
        "action_plan": "行动计划",
        "link_checks": "链接核验证据",
    }
    for artifact_key, (_label, _) in ARTIFACT_SPECS.items():
        record = result.artifact_records.get(artifact_key)
        path = result.artifacts.get(artifact_key)
        if record is None and path is None:
            continue
        file_name = record.display_name if record is not None else path.name
        cards.append(
            f'<div class="ui-download-card"><strong>{labels[artifact_key]}</strong>'
            f"<span>{ARTIFACT_DESCRIPTIONS[artifact_key]}</span>"
            f"<code>{file_name}</code></div>"
        )
    return '<div class="ui-download-grid">' + "".join(cards) + "</div>"


def job_result_rows(result: UiRunResult) -> list[dict[str, Any]]:
    matches = {match.job_id: match for match in result.matches}
    rows: list[dict[str, Any]] = []
    for job in _delivered_jobs(result):
        receipt = _link_receipt(job)
        match = matches.get(job.url)
        rows.append(
            {
                "岗位": job.title,
                "公司": job.company,
                "正文来源": body_source_text(job),
                "链接核验": link_verification_text(job),
                "核验时间": _receipt_value(receipt, "checked_at"),
                "核验方式": _receipt_value(receipt, "method"),
                "最终URL": _receipt_value(receipt, "final_url"),
                "梯队": job.tier or "—",
                "类别": job.category or "—",
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
        category = category.strip()
        detail_text = detail.strip() if separator else raw_error
        if not separator and category in DIAGNOSTIC_CATEGORY_LABELS:
            detail_text = humanize_terminal_reason(category)
        rows.append(
            {
                "类别": DIAGNOSTIC_CATEGORY_LABELS.get(category, category),
                "目标": str(item.get("url", "")),
                "详情": detail_text,
            }
        )
    return rows


def source_status_rows(result: UiRunResult) -> list[dict[str, Any]]:
    labels = {"healthy": "请求成功", "empty": "无结果", "failed": "请求失败"}
    return [
        {
            "来源": str(item.get("source", "")),
            "状态": ("无匹配候选" if item.get("status") == "healthy" and item.get("candidate_count") == 0
                   else labels.get(str(item.get("status", "")), str(item.get("status", "")))),
            "发现": _as_int(item.get("count")),
            "筛选后": _as_int(item.get("candidate_count"), _as_int(item.get("count"))),
            "尝试": _as_int(item.get("attempts")),
            "说明": str(item.get("error_detail", "")),
        }
        for item in result.source_diagnostics
    ]


def artifact_download_spec(
    result: UiRunResult,
    artifact_key: str,
    *,
    artifact_store: ArtifactStore | None = None,
    owner_id: str | None = None,
) -> tuple[str, str, str, bytes]:
    label, mime = ARTIFACT_SPECS[artifact_key]
    file_name, content = read_download_artifact(
        result,
        artifact_key,
        artifact_store=artifact_store,
        owner_id=owner_id,
    )
    return label, file_name, mime, content


# Model and Search API enabled runs share one allowance per session.
LLM_SESSION_LIMIT = 30


def llm_budget_remaining(budget: dict) -> int:
    return max(0, LLM_SESSION_LIMIT - int(budget.get("llm_runs", 0)))


def should_consume_llm_budget(request: UiRunRequest) -> bool:
    """真实 provider 的请求预算与岗位数据来源无关。"""
    uses_llm = bool(request.llm_match_provider or request.llm_extractor_provider
                    or (request.planner_provider and request.planner_strategy != "policy"))
    return uses_llm or request.data_mode == "web_search"


def consume_llm_budget(budget: dict) -> None:
    if llm_budget_remaining(budget) <= 0:
        raise UiRequestError(
            f"本会话 LLM 运行次数已达上限({LLM_SESSION_LIMIT} 次)——"
            "可切换官方来源并关闭模型选项继续体验。"
        )
    budget["llm_runs"] = int(budget.get("llm_runs", 0)) + 1


def demo_mode_notice(result: UiRunResult) -> str:
    """Demo 模式结果必须诚实标注:数据是内置夹具,不是真实岗位。"""
    if result.data_mode != "demo":
        return ""
    return (
        "⚠️ **演示数据**：本次结果来自内置夹具页面（example.com），不是真实岗位。"
        "要跑真实招聘数据，请在左侧数据模式中切换为「实时岗位（官方 API）」。"
    )


def metric_grid_html(metrics: RunMetrics) -> str:
    values = (
        ("有效岗位", metrics.valid_jobs, "success"),
        ("访问页面", metrics.pages_visited, "neutral"),
        ("失败页面", metrics.failed_pages, "warning" if metrics.failed_pages else "neutral"),
        ("重复岗位", metrics.duplicate_jobs, "warning" if metrics.duplicate_jobs else "neutral"),
    )
    items = "".join(
        f'<div class="ui-metric ui-metric-{tone}" aria-label="{label} {value}"><span>{label}</span><strong>{value}</strong></div>'
        for label, value, tone in values
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
  border: 1px solid #DCE5DF;
  border-radius: 12px;
  background: #FFFFFF;
  padding: 0.9rem 1rem;
  box-shadow: 0 10px 30px rgba(20, 47, 34, .06);
}}
.ui-metric span {{ display: block; color: #66736D; font-size: 0.78rem; margin-bottom:.35rem; }}
.ui-metric strong {{ display: block; color: #16211D; font-size: 1.55rem; letter-spacing:-.04em; }}
.ui-metric-success {{ border-top: 3px solid #18794E; }}
.ui-metric-warning {{ border-top: 3px solid #D68B2B; }}
.ui-metric-neutral {{ border-top: 3px solid #9CA8A1; }}
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
    st.markdown(app_styles(), unsafe_allow_html=True)
    passcode = configured_app_passcode(
        environ=os.environ,
        secrets=_streamlit_secrets(st),
    )
    if passcode and not st.session_state.get("app_access_granted"):
        st.markdown(
            '<div class="ui-brand"><div><div class="ui-eyebrow">Private workspace</div>'
            '<div class="ui-brand-mark"><span class="ui-brand-dot"></span>'
            "<h1>岗位 Agent 运行台</h1></div>"
            "<p>输入访问口令后查看和创建共享历史运行。</p></div></div>",
            unsafe_allow_html=True,
        )
        with st.form("app-access-form"):
            supplied_passcode = st.text_input("访问口令", type="password")
            access_submitted = st.form_submit_button(
                "进入运行台",
                type="primary",
                width="stretch",
            )
        if access_submitted:
            if app_access_granted(passcode, supplied_passcode):
                st.session_state["app_access_granted"] = True
                st.rerun()
            st.error("访问口令不正确。")
        return

    owner_id = str(os.environ.get("APP_OWNER_ID", "demo")).strip() or "demo"
    persistence_root = os.environ.get("PERSISTENCE_ROOT", DEFAULT_PERSISTENCE_ROOT)
    run_store, artifact_store = build_configured_persistence(
        persistence_root,
        environ=os.environ,
    )
    cleanup_local_runs("streamlit-runs")
    artifact_store.cleanup_expired()
    run_store.cleanup_expired()
    history_records = run_store.list_runs(owner_id, limit=10)
    history_labels = {
        f"{record.run_id} · {RUN_STATUS_LABELS.get(record.status, record.status)}": record.run_id
        for record in history_records
    }

    with st.sidebar:
        st.markdown(
            '<div class="ui-sidebar-brand"><strong>岗位 Agent</strong><span>实时发现 · 可解释匹配</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="ui-sidebar-label">历史运行</div>', unsafe_allow_html=True)
        if history_labels:
            history_key = f"history-select-{owner_id}"
            if st.session_state.get("latest_ui_owner") == owner_id:
                latest_id = st.session_state.get("latest_run_id")
                latest_label = next((label for label, value in history_labels.items() if value == latest_id), None)
                if latest_label:
                    st.session_state[history_key] = latest_label

            def select_history():
                st.session_state["latest_run_id"] = history_labels[st.session_state[history_key]]
                st.session_state["latest_ui_owner"] = owner_id

            selected_history_label = st.selectbox(
                "最近 10 次",
                list(history_labels),
                key=history_key,
                on_change=select_history,
            )
            selected_run_id = history_labels[selected_history_label]
            st.dataframe(recent_run_rows(history_records), hide_index=True, width="stretch")
        else:
            selected_run_id = None
            st.caption("当前 owner 还没有持久化运行记录。")
        st.markdown('<div class="ui-sidebar-label">数据来源</div>', unsafe_allow_html=True)
        mode_label = st.segmented_control(
            "数据模式",
            list(MODE_LABELS),
            default="实时岗位（官方 API）",
        )
        mode_key = MODE_LABELS[mode_label or "实时岗位（官方 API）"]
        st.markdown(
            f'<div class="ui-mode-hint">{mode_hint(mode_key)}</div>', unsafe_allow_html=True
        )
        remaining = llm_budget_remaining(st.session_state.setdefault(f"llm_budget-{owner_id}", {}))
        budget_caption = st.empty()
        budget_caption.caption(f"本会话模型／搜索额度：剩余 {remaining} / {LLM_SESSION_LIMIT} 次")
        if mode_key == "demo":
            st.caption("内置 Demo 是次要入口：链接指向 example.com，仅用于演示链路。")
        official_specs = None
        if mode_key == "official":
            st.caption(f"保留 {len(VERIFIED_SOURCES)} 个实测来源 · {VERIFIED_SOURCE_DATE} 核验，默认小米。")
            official_specs = st.multiselect(
                "官方发现源",
                list(VERIFIED_SOURCES),
                default=list(DEFAULT_OFFICIAL_SPECS),
                format_func=lambda source: VERIFIED_SOURCES[source]["name"],
            )
            with st.expander("来源核验范围"):
                st.caption(VERIFIED_SOURCE_SCOPE)
                for source in VERIFIED_SOURCES.values():
                    st.markdown(f"[{source['name']}官方入口]({source['official_url']})")
                    st.caption(f"抽查关键词：{source.get('validation_keyword', 'AI Agent 实习')}。"
                               f"{source.get('note', '')}")
            with st.expander("其他大厂来源的限制"):
                for name, reason in SOURCE_LIMITATIONS.items():
                    st.caption(f"{name}：{reason}")
                st.caption("可在网页搜索中手动填写企业招聘域名，或指定已知的岗位详情 URL。")
        if mode_key == "web_search":
            st.caption("默认雇主：" + "、".join(source["name"] for source in VERIFIED_SOURCES.values()) + "。")
        st.markdown('<div class="ui-sidebar-label">模型与执行</div>', unsafe_allow_html=True)
        with st.expander("高级设置（模型与执行）", expanded=False):
            model_providers = [provider for provider in UI_LLM_PROVIDERS
                               if str(os.environ.get(PROVIDER_API_KEY_ENV[provider], "")).strip()]
            if not model_providers:
                st.caption("未配置模型服务，当前可使用规则抽取与匹配。")
            use_llm_extractor = bool(st.toggle("启用 LLM 抽取", value=mode_key == "web_search",
                                              disabled=not model_providers) and model_providers)
            extractor_provider = (
                st.selectbox("抽取模型", model_providers)
                if use_llm_extractor else None
            )
            use_llm_match = bool(st.toggle("启用 LLM 匹配", value=False,
                                          disabled=not model_providers) and model_providers)
            match_provider = (
                st.selectbox("匹配模型", model_providers)
                if use_llm_match else None
            )
            if use_llm_match:
                st.caption(f"开启后，简历文字会发送给 {match_provider} 模型服务。")
            execution_mode = st.selectbox("执行方式", ["Agent", "固定流程（对照）"])
            engine = "hybrid" if execution_mode == "Agent" else "workflow"
            hitl = st.toggle("保存前确认", value=False, disabled=engine != "hybrid")
            use_planner = bool(st.toggle("启用模型选择候选", value=False,
                                         disabled=engine != "hybrid" or not model_providers)
                               and model_providers and engine == "hybrid")
            planner_provider = (
                st.selectbox("规划模型", model_providers)
                if use_planner else None
            )
            if "deepseek" in model_providers:
                st.divider()
                st.caption("用合成文字和测试图检查模型，不使用简历；会产生少量 API 用量。")
                if st.button("检测 DeepSeek 文本与视觉", width="stretch"):
                    try:
                        consume_llm_budget(st.session_state.setdefault(f"llm_budget-{owner_id}", {}))
                    except UiRequestError as exc:
                        st.error(str(exc))
                    else:
                        with st.spinner("正在检查文本与图片识别…"):
                            st.session_state["deepseek_health"] = check_deepseek_capabilities()
                health = st.session_state.get("deepseek_health")
                if health:
                    for kind, label in [("text", "文本推理"), ("vision", "图片识别")]:
                        outcome = health[kind]
                        if outcome["ok"]:
                            st.success(f"{label}通过 · {outcome['elapsed_seconds']} 秒")
                        else:
                            status = outcome.get("http_status")
                            reason = f"HTTP {status}" if status else "未得到正确识别结果或连接失败"
                            st.error(f"{label}未通过：{reason}")

    st.markdown('<div class="ui-section-label">简历</div>', unsafe_allow_html=True)
    resume_upload = st.file_uploader(
        "简历文件", type=["pdf", "md", "txt"], key="resume-upload"
    )
    sync_resume_preview(st.session_state, resume_upload)
    upload_error = str(st.session_state.get(RESUME_UPLOAD_ERROR_KEY, "") or "")
    if upload_error:
        st.error(
            f"简历文件无法使用：{upload_error} "
            "本次运行已阻止；请更换文件，或移除文件后直接粘贴简历文字。"
        )
    st.text_area(
        "简历文字预览",
        key=RESUME_PREVIEW_TEXT_KEY,
        height=220,
        placeholder="上传 PDF 后会自动提取；也可以直接粘贴或修改简历文字。",
    )
    st.caption("上传后立即抽取，可在搜索前修改；更换或移除文件会清空上一份文字。")
    if use_llm_match:
        st.caption(
            f"当前已启用 LLM 匹配：运行时会把这页简历文字发送给 {match_provider} 模型服务。"
        )
    else:
        st.caption("默认本地规则匹配：简历文字不会发送给模型服务。")

    with st.form("agent-run-form"):
        st.markdown('<div class="ui-form-title">配置一次岗位扫描</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="ui-form-caption">先设定搜索范围，再让 Agent 发现、验证并解释岗位匹配。</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="ui-section-label">搜索条件</div>', unsafe_allow_html=True)
        first, second = st.columns(2)
        with first:
            keyword = st.text_input("岗位关键词", value="AI Agent 实习")
            st.caption(
                "关键词按空格或逗号分隔，每项都须在岗位标题或正文中命中；"
                "支持 AI/人工智能、Agent/智能体、实习/intern 等明确别名。"
            )
            skills_text = st.text_input("技能标签", value="")
        with second:
            location = st.text_input("地点", value="全国")
            st.caption(
                "按岗位工作地严格过滤：多个城市用逗号或顿号分隔，命中任一城市即可；"
                "排除异地和地点未知岗位，全国/不限不限制地点。"
            )
            target_count = st.number_input(
                "目标岗位数", min_value=1, max_value=50, value=DEFAULT_TARGET_COUNT, step=1
            )
            # Include discovery/model overhead and a per-target browser budget.
            # Extra targets cannot expand this run beyond its 10-candidate check cap.
            deadline_seconds = 60 + 30 * min(int(target_count), 10)
            st.caption(
                f"按目标岗位数自动分配总时限 {deadline_seconds} 秒，达到目标会提前结束；"
                "本轮最多检查 10 个候选，未检查部分会明确显示。"
            )
        web_search_hosts = []
        if mode_key == "web_search":
            extra_employers = st.text_input(
                "额外公司招聘域名（可选，最多 3 个）",
                placeholder="例如 careers.example.com",
                help="填写你想加入的公司的官方招聘完整域名，用逗号或空格分隔。共享招聘平台需要公司的专属子域名。",
            )
            web_search_hosts = list(dict.fromkeys(filter(None, re.split(r"[\s,，、]+", extra_employers.strip()))))
        aggregator_upload = None
        aggregator_url_text = ""
        seed_url_text = ""
        if mode_key == "aggregator":
            st.caption(
                "两种数据来源二选一：上传本地聚合 JSON，或直接拉取聚合仓库发布的"
                " jobs.json（默认已填 job-radar 真实岗位库）。"
            )
            aggregator_upload = st.file_uploader("岗位聚合文件", type=["json"])
            aggregator_url_text = st.text_input("聚合数据 URL", value=DEFAULT_AGGREGATOR_URL)
        elif mode_key == "seed_urls":
            seed_url_text = st.text_area("岗位 URL", height=120)
        st.markdown('<div class="ui-section-label">数据输入</div>', unsafe_allow_html=True)
        submitted = st.form_submit_button("开始运行岗位 Agent", type="primary", width="stretch")

    resume_blocker = resume_run_blocker(st.session_state) if submitted else ""
    if submitted and resume_blocker:
        st.error(f"{resume_blocker} 本次运行未启动。")
    if submitted and not resume_blocker:
        temporary_path: Path | None = None
        try:
            if mode_key == "official" and not official_specs:
                raise UiRequestError("请至少选择一个已核验的官方发现源。")
            combined_resume = str(
                st.session_state.get(RESUME_PREVIEW_TEXT_KEY, "") or ""
            ).strip()
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
                data_mode=mode_key,
                aggregator_path=str(temporary_path) if temporary_path else None,
                aggregator_url=aggregator_url_text.strip() or None,
                official_specs=official_specs or None,
                web_search_hosts=web_search_hosts,
                seed_urls=parse_seed_urls(seed_url_text),
                llm_extractor_provider=extractor_provider,
                llm_match_provider=match_provider,
                engine=engine,
                hitl=hitl and engine == "hybrid",
                planner_strategy="adaptive" if use_planner and engine == "hybrid" else "policy",
                planner_provider=planner_provider if engine == "hybrid" else None,
                deadline_seconds=float(deadline_seconds),
            )
            validate_ui_request(request, environ=os.environ)
            if should_consume_llm_budget(request):
                consume_llm_budget(
                    st.session_state.setdefault(f"llm_budget-{owner_id}", {})
                )
            with st.spinner("Agent 正在运行..."):
                completed_run = asyncio.run(
                    run_ui_request(
                        request,
                        environ=os.environ,
                        run_store=run_store,
                        artifact_store=artifact_store,
                        owner_id=owner_id,
                    )
                )
                st.session_state["latest_run_id"] = completed_run.run_id
                st.session_state["latest_ui_owner"] = owner_id
        except (UiRequestError, ValueError, OSError) as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"任务运行失败：{type(exc).__name__}: {exc}")
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    remaining = llm_budget_remaining(st.session_state.setdefault(f"llm_budget-{owner_id}", {}))
    budget_caption.caption(f"本会话模型／搜索额度：剩余 {remaining} / {LLM_SESSION_LIMIT} 次")

    # Migrate old browser sessions; runtime and result objects are reconstructed from storage.
    st.session_state.pop("latest_ui_result", None)
    view_run_id = (st.session_state.get("latest_run_id")
                   if st.session_state.get("latest_ui_owner") == owner_id else None) or selected_run_id
    result = load_persisted_ui_result(run_store, artifact_store, owner_id, view_run_id) if view_run_id else None

    st.markdown(
        brand_header_html(result if isinstance(result, UiRunResult) else None),
        unsafe_allow_html=True,
    )
    if isinstance(result, UiRunResult):
        if result.run_status == "awaiting_approval" and result.approval:
            st.info(f"请确认保存 {result.approval['job_count']} 个岗位。拒绝后不会写入岗位库。")
            approve_col, reject_col = st.columns(2)
            approval_id = str(result.approval["approval_id"])
            st.caption(f"审批编号：{approval_id}")
            st.caption(f"结果批次校验：{result.approval.get('payload_hash', '')}")
            approve = approve_col.button("确认保存", key=f"approve-{result.run_id}-{approval_id}")
            reject = reject_col.button("拒绝保存", key=f"reject-{result.run_id}-{approval_id}")
            if approve or reject:
                try:
                    resumed_run = asyncio.run(resume_ui_request(
                        result.run_id,
                        ApprovalDecision(approval_id=approval_id, outcome="approve" if approve else "reject"),
                        owner_id=owner_id, environ=os.environ,
                        run_store=run_store, artifact_store=artifact_store,
                    ))
                    st.session_state["latest_run_id"] = resumed_run.run_id
                    st.session_state["latest_ui_owner"] = owner_id
                    st.rerun()
                except (ValueError, OSError, RuntimeError) as exc:
                    st.error(f"确认未完成：{type(exc).__name__}")
        _render_result(
            st,
            result,
            artifact_store=artifact_store,
            owner_id=owner_id,
        )
    elif view_run_id:
        record = run_store.get_run(owner_id, view_run_id)
        if record is not None and record.status == "failed":
            st.error(record.error_summary or "该运行失败，没有可展示的产物。")
        elif record is not None:
            st.info(f"运行 {record.run_id} 当前状态：{RUN_STATUS_LABELS.get(record.status, record.status)}")


def _render_result(
    st: Any,
    result: UiRunResult,
    *,
    artifact_store: ArtifactStore | None = None,
    owner_id: str | None = None,
) -> None:
    status, detail, tone = run_status(result)
    st.markdown(
        f'<div class="ui-result-header"><div><h2>运行结果</h2><p>{detail} · Run ID {result.run_id}</p></div>'
        f'<span class="ui-result-chip">{status}</span></div>',
        unsafe_allow_html=True,
    )
    notice = demo_mode_notice(result)
    if notice:
        st.warning(notice)
    st.caption(saved_status_text(result))
    st.markdown(
        f'<div class="ui-progress-note">{_escaped(progress_summary_text(result))}</div>',
        unsafe_allow_html=True,
    )
    if result.run_status in {"partial", "failed"}:
        reason_notice = terminal_reason_notice(result)
        if reason_notice:
            st.info(reason_notice + "。")
    st.markdown(metric_grid_html(result.metrics), unsafe_allow_html=True)
    if result.search_conditions:
        st.caption(
            f"本次结果条件：关键词 {result.search_conditions['keyword']}（全部命中），"
            f"工作地点 {result.search_conditions['location']}（多个城市命中任一即可）。"
        )

    jobs_tab, diagnostics_tab, trace_tab, downloads_tab = st.tabs(
        ["岗位结果", "失败与诊断", "执行轨迹", "下载"]
    )
    with jobs_tab:
        pending_rows = pending_job_rows(result)
        if pending_rows:
            st.markdown(pending_jobs_html(result), unsafe_allow_html=True)
            with st.expander(f"查看未交付候选明细（{len(pending_rows)}）"):
                st.dataframe(pending_rows, width="stretch", hide_index=True)
        st.caption(
            "正文来源说明岗位内容从哪里取得；链接核验显示实际页面的检查结果、"
            "检查时间和最终地址。匹配分仅表示已识别技能的覆盖率，岗位条件仍需核对原文。"
        )
        rows = job_result_rows(result)
        cards = job_cards_html(result)
        if cards:
            st.markdown(cards, unsafe_allow_html=True)
        if rows:
            st.markdown("#### 表格视图")
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
            st.info(empty_result_explanation(result))
    with diagnostics_tab:
        source_rows = source_status_rows(result)
        if source_rows:
            st.markdown("#### 来源状态")
            st.dataframe(source_rows, width="stretch", hide_index=True)
        rows = diagnostic_rows(result)
        if rows:
            st.dataframe(rows, width="stretch", hide_index=True)
        else:
            st.success("本次运行没有来源或页面诊断。")
    with trace_tab:
        if result.execution_trace:
            st.dataframe(result.execution_trace, width="stretch", hide_index=True)
        else:
            st.info("本次运行没有执行轨迹。")
    with downloads_tab:
        st.markdown(artifact_cards_html(result), unsafe_allow_html=True)
        for artifact_key in ARTIFACT_SPECS:
            if artifact_key not in result.artifacts and artifact_key not in result.artifact_records:
                continue
            label, file_name, mime, content = artifact_download_spec(
                result,
                artifact_key,
                artifact_store=artifact_store,
                owner_id=owner_id,
            )
            st.download_button(
                label,
                data=content,
                file_name=file_name,
                mime=mime,
                key=f"download-{result.run_id}-{artifact_key}",
            )


if __name__ == "__main__":
    main()
