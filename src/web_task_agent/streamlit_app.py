from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Callable, Mapping
from hmac import compare_digest
from pathlib import Path
from typing import Any

from web_task_agent.artifact_store import (
    ArtifactStore,
    FileArtifactStore,
    S3ArtifactStore,
)
from web_task_agent.models import RunMetrics
from web_task_agent.run_store import FileRunStore, PostgresRunStore, RunRecord, RunStore
from web_task_agent.streamlit_runner import (
    DEFAULT_AGGREGATOR_URL,
    UiRequestError,
    UiRunRequest,
    UiRunResult,
    extract_resume_upload,
    load_persisted_ui_result,
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

MODE_HINTS = {
    "demo": "内置夹具数据：适合快速查看完整链路，不访问公网。",
    "official": "实时连接官方招聘 API：按来源发现岗位，再验证正文和可打开链接。",
    "aggregator": "聚合岗位：从上传文件或 jobs.json 读取岗位，再执行统一验证和匹配。",
    "seed_urls": "指定岗位 URL：适合验证单个或一组真实岗位页面。",
}

OFFICIAL_SPEC_OPTIONS = [
    "tencent-campus",
    "meituan",
    "unitree",
    "xiaomi",
    "netease",
    "xiaohongshu",
    "mihoyo",
    "xpeng",
    "agibot",
    "galaxea",
    "robotera",
    "fourier",
    "ubtech",
    "jd",
    "pinduoduo",
    "ctrip",
    "huawei",
    "nio",
    "liauto",
    "byd",
    "geely",
    "baidu",
    "bing-serp",
    "zhongqi",
    "xinghai",
    "tarsrobot",
    "x2robot",
    "limx",
    "ai2robotics",
    "astribot",
    "dexmal",
    "booster",
    "deeprobotics",
    "moonshot",
    "zhipu",
    "minimax",
    "stepfun",
    "deepseek",
    "baichuan",
    "modelbest",
    "shengshu",
    "enflame",
]

ARTIFACT_DESCRIPTIONS = {
    "json": "机器可读的完整运行状态、岗位、匹配和诊断。",
    "report": "本次运行的可复核结果，适合阅读和分享。",
    "dashboard": "可筛选岗位、匹配分和执行轨迹的 HTML 页面。",
    "action_plan": "根据岗位缺口生成的投递和技能补强计划。",
}

DEFAULT_PERSISTENCE_ROOT = "streamlit-persistence"
THEME_COLORS = {
    "canvas": "#F4F7F5",
    "surface": "#FFFFFF",
    "ink": "#16211D",
    "muted": "#53645C",
    "border": "#CEDAD3",
    "brand": "#117A52",
    "sidebar": "#10271E",
    "sidebar_text": "#EAF4EE",
    "sidebar_muted": "#AEC3B7",
    "warning": "#9A5A0A",
    "error": "#B42318",
}
RUN_STATUS_LABELS = {
    "queued": "排队中",
    "running": "运行中",
    "succeeded": "已完成",
    "partial": "部分完成",
    "failed": "失败",
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


def run_status(result: UiRunResult | None) -> tuple[str, str, str]:
    if result is None:
        return "待运行", "配置任务后开始一次岗位扫描", "ready"
    failure_count = max(result.metrics.failed_pages, len(result.diagnostics))
    if failure_count:
        return (
            "已完成",
            f"{result.metrics.valid_jobs} 个有效岗位 · {failure_count} 个页面失败",
            "warning",
        )
    return "已完成", f"{result.metrics.valid_jobs} 个有效岗位 · 页面访问正常", "success"


def app_styles() -> str:
    return """
<style>
:root {
  --ui-ink: #16211d;
  --ui-muted: #53645c;
  --ui-border: #cedad3;
  --ui-surface: #ffffff;
  --ui-canvas: #f4f7f5;
  --ui-green: #117a52;
  --ui-green-soft: #e7f3ec;
  --ui-sidebar: #10271e;
  --ui-sidebar-text: #EAF4EE;
  --ui-sidebar-muted: #AEC3B7;
  --ui-warning: #9a5a0a;
  --ui-warning-soft: #fff5e5;
  --ui-error: #b42318;
  --ui-shadow: 0 10px 30px rgba(20, 47, 34, .06);
}
.stApp { background: var(--ui-canvas); color: var(--ui-ink); }
[data-testid="stHeader"] { background: transparent; }
.block-container { max-width: 1440px; padding-top: 2.4rem; padding-bottom: 4rem; }
[data-testid="stSidebar"] { background: var(--ui-sidebar); border-right: 1px solid #243b30; }
[data-testid="stSidebar"] > div:first-child { padding: 1.4rem 1rem 2rem; }
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] strong,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color: var(--ui-sidebar-text); }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p,
[data-testid="stSidebar"] .ui-sidebar-caption { color: var(--ui-sidebar-muted) !important; }
[data-testid="stSidebar"] [data-baseweb="select"],
[data-testid="stSidebar"] [data-baseweb="input"],
[data-testid="stSidebar"] [data-testid="stTextInputRootElement"],
[data-testid="stSidebar"] [data-testid="stNumberInputContainer"] { background: #20362b; border-color: #365545; }
[data-testid="stSidebar"] [data-baseweb="select"] *,
[data-testid="stSidebar"] input { color: #f2f8f3 !important; }
.ui-workspace-header { margin-bottom:1.35rem; }
.ui-brand { display:flex; align-items:flex-start; justify-content:space-between; gap:1.5rem; margin-bottom:2rem; }
.ui-brand-mark { display:flex; align-items:center; gap:.7rem; }
.ui-brand-dot { width:13px; height:13px; border-radius:4px; background:var(--ui-green); box-shadow:0 0 0 5px #d8efe1; }
.ui-eyebrow { color:var(--ui-green); font-size:.72rem; font-weight:800; letter-spacing:.12em; text-transform:uppercase; margin-bottom:.35rem; }
.ui-brand h1 { color:var(--ui-ink); font-size:2rem; line-height:1.1; letter-spacing:-.04em; margin:0; }
.ui-brand p { color:var(--ui-muted); margin:.45rem 0 0; font-size:.95rem; }
.ui-status { display:inline-flex; align-items:center; gap:.55rem; white-space:nowrap; border:1px solid var(--ui-border); border-radius:999px; background:var(--ui-surface); padding:.5rem .8rem; color:var(--ui-muted); font-size:.82rem; box-shadow:var(--ui-shadow); }
.ui-status strong { color:var(--ui-ink); }
.ui-status-dot { width:8px; height:8px; border-radius:50%; background:#9ca8a1; }
.ui-status-success .ui-status-dot { background:var(--ui-green); }
.ui-status-warning .ui-status-dot { background:#d68b2b; }
.ui-sidebar-brand { padding:.3rem .3rem 1.3rem; border-bottom:1px solid #2b4637; margin-bottom:1.2rem; }
.ui-sidebar-brand strong { display:block; color:#fff; font-size:1.1rem; letter-spacing:-.02em; }
.ui-sidebar-brand span { color:#9eb5a7; font-size:.78rem; }
.ui-sidebar-label { color:#8faa9b; font-size:.67rem; font-weight:800; letter-spacing:.13em; text-transform:uppercase; margin:1.2rem .2rem .55rem; }
.ui-mode-hint { border:1px solid rgba(148, 191, 164, .26); background:rgba(103, 157, 119, .12); border-radius:10px; padding:.65rem .7rem; color:#c7dbcd; font-size:.78rem; line-height:1.45; margin:.55rem 0 1rem; }
[data-testid="stForm"] { border:1px solid var(--ui-border); border-radius:8px; background:var(--ui-surface); box-shadow:var(--ui-shadow); padding:1.35rem 1.45rem 1.5rem; }
.ui-form-title { color:var(--ui-ink); font-size:1.05rem; font-weight:750; margin-bottom:.15rem; }
.ui-form-caption { color:var(--ui-muted); font-size:.84rem; margin-bottom:1.1rem; }
.ui-section-label { color:var(--ui-green); font-size:.7rem; font-weight:800; letter-spacing:.1em; text-transform:uppercase; margin:1rem 0 .45rem; }
.ui-result-header { display:flex; align-items:flex-end; justify-content:space-between; gap:1rem; margin:2rem 0 .9rem; }
.ui-result-header h2 { color:var(--ui-ink); margin:0; letter-spacing:-.03em; }
.ui-result-header p { color:var(--ui-muted); margin:.25rem 0 0; font-size:.86rem; }
.ui-result-chip { border-radius:999px; background:var(--ui-green-soft); color:var(--ui-green); padding:.35rem .65rem; font-size:.75rem; font-weight:700; }
.ui-metric-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:.8rem; margin:0 0 1.1rem; }
.ui-metric { border:1px solid var(--ui-border); border-radius:8px; background:var(--ui-surface); padding:.9rem 1rem; box-shadow:var(--ui-shadow); }
.ui-metric span { display:block; color:var(--ui-muted); font-size:.78rem; margin-bottom:.35rem; }
.ui-metric strong { display:block; color:var(--ui-ink); font-size:1.55rem; letter-spacing:-.04em; }
.ui-metric-success { border-top:3px solid var(--ui-green); }
.ui-metric-warning { border-top:3px solid #d68b2b; }
.ui-metric-neutral { border-top:3px solid #9ca8a1; }
.ui-empty { border:1px dashed var(--ui-border); border-radius:8px; background:#fbfcfb; padding:1.4rem; color:var(--ui-muted); text-align:center; }
.ui-download-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:.8rem; margin-bottom:1rem; }
.ui-download-card { border:1px solid var(--ui-border); border-radius:8px; background:var(--ui-surface); padding:1rem; min-height:116px; }
.ui-download-card strong { display:block; color:var(--ui-ink); margin-bottom:.3rem; }
.ui-download-card span { display:block; color:var(--ui-muted); font-size:.8rem; line-height:1.45; }
.ui-download-card code { display:block; color:#7a877e; font-size:.7rem; margin-top:.55rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }

.ui-config-kicker {
  color: var(--ui-sidebar-muted);
  font-size: .68rem;
  font-weight: 800;
  letter-spacing: .13em;
  text-transform: uppercase;
  margin-bottom: .25rem;
}
.ui-config-title { color: var(--ui-sidebar-text); margin:0 0 .35rem; font-size:1.25rem; letter-spacing:0; }
.ui-config-copy { color: var(--ui-sidebar-muted); font-size:.8rem; line-height:1.5; margin-bottom:1rem; }
.ui-panel-label {
  color: var(--ui-sidebar-muted);
  font-size:.68rem;
  font-weight:800;
  letter-spacing:.12em;
  text-transform:uppercase;
  margin:1.1rem 0 .45rem;
}
.ui-result-shell { min-height:1px; }
[data-testid="stAlertContentWarning"],
[data-testid="stAlertContentWarning"] * {
  color: var(--ui-warning) !important;
  -webkit-text-fill-color: var(--ui-warning) !important;
}
.ui-empty-state {
  border:1px dashed var(--ui-border);
  border-radius:8px;
  background:var(--ui-surface);
  min-height:420px;
  padding:2.2rem;
  display:flex;
  flex-direction:column;
  justify-content:center;
}
.ui-empty-state h3 { color:var(--ui-ink); font-size:1.35rem; margin:0 0 .45rem; letter-spacing:0; }
.ui-empty-state p { color:var(--ui-muted); font-size:.88rem; line-height:1.55; margin:0; max-width:52ch; }
.ui-empty-state .ui-empty-step {
  color:var(--ui-green);
  font-size:.68rem;
  font-weight:800;
  letter-spacing:.12em;
  text-transform:uppercase;
  margin-bottom:.75rem;
}
.ui-empty-state .ui-empty-flow {
  display:flex;
  flex-wrap:wrap;
  gap:.45rem;
  margin-top:1.25rem;
}
.ui-empty-state .ui-empty-flow span {
  border:1px solid var(--ui-border);
  border-radius:8px;
  padding:.4rem .6rem;
  color:var(--ui-muted);
  font-size:.74rem;
}

div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child {
  background: var(--ui-sidebar);
  border-radius: 8px;
  padding: 1rem 1rem 1.15rem;
  position: sticky;
  top: 1rem;
  align-self: flex-start;
  max-height: calc(100vh - 2rem);
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: thin;
}
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child label,
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child h2,
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child h3,
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [data-testid="stMarkdownContainer"] p {
  color: var(--ui-sidebar-text);
}
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [data-testid="stCaptionContainer"] p {
  color: var(--ui-sidebar-muted) !important;
}
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [data-testid="stForm"] {
  background: transparent;
  border: 0;
  border-radius: 0;
  box-shadow: none;
  padding: 0;
}
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [data-testid="stButtonGroup"] {
  width: 100%;
}
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [role="radiogroup"] {
  display: flex;
  flex-wrap: wrap;
  gap: .35rem;
  width: 100%;
  background: #17382b;
  border-radius: 8px;
  padding: .3rem;
}
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [role="radio"] {
  flex: 1 1 calc(50% - .35rem);
  min-width: 0;
  height: auto;
  min-height: 2.1rem;
  border: 1px solid #4b705d;
  border-radius: 6px;
  background: #234a38;
}
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [role="radio"] p {
  color: var(--ui-sidebar-text) !important;
  white-space: normal;
  line-height: 1.25;
  font-size: .76rem;
}
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [role="radio"][data-selected="true"] {
  background: #ffffff;
  border-color: #ffffff;
}
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [role="radio"][data-selected="true"] p {
  color: var(--ui-ink) !important;
}
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [data-baseweb="select"],
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [data-baseweb="input"],
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [data-testid="stNumberInputContainer"],
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [data-testid="stFileUploaderDropzone"],
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child textarea {
  background: #ffffff !important;
  border-color: #aebdb5 !important;
}
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child input,
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child textarea,
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [data-baseweb="select"] * {
  color: var(--ui-ink) !important;
  -webkit-text-fill-color: var(--ui-ink) !important;
}
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [data-testid="stFileUploaderDropzone"] * {
  color: var(--ui-ink) !important;
  -webkit-text-fill-color: var(--ui-ink) !important;
}
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [data-testid="stFormSubmitButton"] button {
  background: var(--ui-green);
  border-color: var(--ui-green);
  color: #ffffff;
  min-height: 2.75rem;
  font-weight: 750;
}
div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child [data-testid="stFormSubmitButton"] button:hover {
  background: #0e6846;
  border-color: #0e6846;
  color: #ffffff;
}
@media (max-width: 760px) {
  .block-container { padding:1.35rem .9rem 3rem; }
  .ui-brand { display:block; margin-bottom:1.35rem; }
  .ui-status { display:inline-flex; margin-top:1rem; }
  .ui-brand h1 { font-size:1.65rem; }
  .ui-metric-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .ui-download-grid { grid-template-columns:1fr; }
  [data-testid="stForm"] { padding:.95rem; }
  div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child {
    position: static;
    padding: .9rem;
    max-height: none;
    overflow: visible;
  }
  .ui-empty-state { min-height:300px; padding:1.35rem; }
}
</style>
"""


def brand_header_html(result: UiRunResult | None, data_mode: str | None = None) -> str:
    status, detail, tone = run_status(result)
    mode = result.data_mode if result is not None else (data_mode or "ready")
    return f"""
<div class="ui-workspace-header">
  <div class="ui-brand">
    <div>
      <div class="ui-eyebrow">AI career workspace</div>
      <div class="ui-brand-mark"><span class="ui-brand-dot"></span><h1>岗位 Agent 运行台</h1></div>
      <p>从官方岗位发现到可解释匹配，把一次求职搜索变成一份可复核结果。</p>
    </div>
    <div class="ui-status ui-status-{tone}"><span class="ui-status-dot"></span><span><strong>{status}</strong> · {detail}</span></div>
  </div>
  <div class="ui-status ui-status-{tone}" style="margin-top:.55rem"><span class="ui-status-dot"></span><span>数据模式 · {mode}</span></div>
</div>
"""


def workspace_empty_state_html() -> str:
    return """
<div class="ui-empty-state">
  <div class="ui-empty-step">运行结果</div>
  <h3>等待一次岗位扫描</h3>
  <p>在左侧配置数据来源、岗位条件和候选人信息。运行完成后，这里会整理岗位列表、匹配解释、诊断轨迹和下载产物。</p>
  <div class="ui-empty-flow">
    <span>岗位列表</span>
    <span>匹配解释</span>
    <span>诊断轨迹</span>
    <span>下载产物</span>
  </div>
</div>
"""


def artifact_cards_html(result: UiRunResult) -> str:
    cards = []
    labels = {
        "json": "JSON 结果",
        "report": "Markdown 报告",
        "dashboard": "HTML Dashboard",
        "action_plan": "行动计划",
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
    verification_labels = {
        "api-verified": "✓ 官方API已验",
        "detail-probed": "✓ 详情已探",
        "list-attested": "列表自证",
        "http-fetched": "✓ HTTP抓取",
        "unverified": "—",
    }
    for job in result.jobs:
        match = matches.get(job.url)
        rows.append(
            {
                "岗位": job.title,
                "公司": job.company,
                "页面核验": verification_labels.get(job.url_verification, "—"),
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
        rows.append(
            {
                "类别": category.strip(),
                "目标": str(item.get("url", "")),
                "详情": detail.strip() if separator else raw_error,
            }
        )
    return rows


def diagnostics_empty_message() -> str:
    return "本次运行没有来源或页面诊断。"


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


# 公开演示成本保护:每会话 LLM 运行次数上限(qwen 额度是真实成本)
LLM_SESSION_LIMIT = 3


def llm_budget_remaining(budget: dict) -> int:
    return max(0, LLM_SESSION_LIMIT - int(budget.get("llm_runs", 0)))


def should_consume_llm_budget(request: UiRunRequest) -> bool:
    """仅当本次运行真的会调 LLM(非 Demo 数据 + 勾选了任一 LLM 开关)才占预算。"""
    uses_llm = bool(request.llm_match_provider or request.llm_extractor_provider)
    return uses_llm and request.data_mode != "demo"


def consume_llm_budget(budget: dict) -> None:
    if llm_budget_remaining(budget) <= 0:
        raise UiRequestError(
            f"本会话 LLM 运行次数已达上限({LLM_SESSION_LIMIT} 次)——"
            "演示额度有限,可切换为内置 Demo 模式继续体验,或稍后重试。"
        )
    budget["llm_runs"] = int(budget.get("llm_runs", 0)) + 1


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
  border: 1px solid #CEDAD3;
  border-radius: 8px;
  background: #FFFFFF;
  padding: 0.9rem 1rem;
  box-shadow: 0 10px 30px rgba(20, 47, 34, .06);
}}
.ui-metric span {{ display: block; color: #53645C; font-size: 0.78rem; margin-bottom:.35rem; }}
.ui-metric strong {{ display: block; color: #16211D; font-size: 1.55rem; letter-spacing:-.04em; }}
.ui-metric-success {{ border-top: 3px solid #117A52; }}
.ui-metric-warning {{ border-top: 3px solid #9A5A0A; }}
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
    st.set_page_config(
        page_title="岗位 Agent 运行台",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(app_styles(), unsafe_allow_html=True)
    passcode = configured_app_passcode(
        environ=os.environ,
        secrets=_streamlit_secrets(st),
    )
    if passcode and not st.session_state.get("app_access_granted"):
        _, gate_column, _ = st.columns([1, 1.3, 1])
        with gate_column:
            st.markdown(
                '<div class="ui-workspace-header"><div class="ui-eyebrow">Private workspace</div>'
                "<h1>岗位 Agent 运行台</h1>"
                "<p>输入访问口令后查看和创建共享历史运行。</p></div>",
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
    artifact_store.cleanup_expired()
    run_store.cleanup_expired()
    history_records = run_store.list_runs(owner_id, limit=10)
    history_labels = {
        f"{record.run_id} · {RUN_STATUS_LABELS.get(record.status, record.status)}": record.run_id
        for record in history_records
    }
    header_slot = st.empty()
    config_column, result_column = st.columns([0.84, 1.46], gap="large")

    with config_column:
        st.markdown('<div class="ui-workspace-config"></div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="ui-config-kicker">Run setup</div>'
            '<h2 class="ui-config-title">配置任务</h2>'
            '<p class="ui-config-copy">先确定数据来源，再设置岗位条件与候选人信息。</p>',
            unsafe_allow_html=True,
        )
        if history_labels:
            selected_history_label = st.selectbox(
                "最近 10 次",
                list(history_labels),
                key=f"history-select-{owner_id}",
            )
            selected_run_id = history_labels[selected_history_label]
            selected_record = next(
                record for record in history_records if record.run_id == selected_run_id
            )
            st.caption(
                f"{selected_record.created_at:%Y-%m-%d %H:%M} · "
                f"{int(selected_record.metrics.get('valid_jobs', 0) or 0)} 个有效岗位"
            )
        else:
            selected_run_id = None
            st.caption("当前 owner 还没有持久化运行记录。")
        st.markdown('<div class="ui-panel-label">数据来源</div>', unsafe_allow_html=True)
        mode_label = st.segmented_control(
            "数据模式",
            ["内置 Demo", "实时岗位（官方 API）", "聚合岗位（上传/URL）", "指定岗位 URL"],
            default="内置 Demo",
        )
        mode_key = {
            "内置 Demo": "demo",
            "实时岗位（官方 API）": "official",
            "聚合岗位（上传/URL）": "aggregator",
            "指定岗位 URL": "seed_urls",
        }[mode_label or "内置 Demo"]
        st.markdown(
            f'<div class="ui-mode-hint">{mode_hint(mode_key)}</div>', unsafe_allow_html=True
        )
        if mode_label == "内置 Demo":
            st.caption("岗位链接指向 example.com，仅用于演示链路。")
        official_specs = None
        if mode_label == "实时岗位（官方 API）":
            st.caption("可按来源缩小扫描范围，减少等待时间。")
            official_specs = st.multiselect(
                "发现源",
                OFFICIAL_SPEC_OPTIONS,
                default=OFFICIAL_SPEC_OPTIONS,
            )
        st.markdown('<div class="ui-panel-label">模型能力</div>', unsafe_allow_html=True)
        use_llm_extractor = st.toggle("启用 LLM 抽取", value=False)
        extractor_provider = (
            st.selectbox("抽取模型", ["qwen", "deepseek"]) if use_llm_extractor else None
        )
        use_llm_match = st.toggle("启用 LLM 匹配", value=False)
        match_provider = st.selectbox("匹配模型", ["qwen", "deepseek"]) if use_llm_match else None

        with st.form("agent-run-form"):
            st.markdown('<div class="ui-panel-label">搜索条件</div>', unsafe_allow_html=True)
            keyword = st.text_input("岗位关键词", value="AI Agent 实习")
            skills_text = st.text_input("技能标签", value="Python, LangGraph, RAG")
            location = st.text_input("地点", value="全国")
            target_count = st.number_input(
                "目标岗位数",
                min_value=1,
                max_value=50,
                value=10,
                step=1,
            )

            st.markdown(
                '<div class="ui-panel-label">候选人信息（可选）</div>',
                unsafe_allow_html=True,
            )
            resume_text = st.text_area(
                "简历文本",
                height=150,
                placeholder="粘贴简历摘要，Agent 会用它解释匹配技能和缺口。",
            )
            resume_upload = st.file_uploader("简历文件", type=["md", "txt", "pdf", "docx"])
            aggregator_upload = None
            aggregator_url_text = ""
            seed_url_text = ""
            if mode_label == "聚合岗位（上传/URL）":
                st.caption(
                    "两种数据来源二选一：上传本地聚合 JSON，或直接拉取聚合仓库发布的"
                    " jobs.json（默认已填 job-radar 真实岗位库）。"
                )
                aggregator_upload = st.file_uploader("岗位聚合文件", type=["json"])
                aggregator_url_text = st.text_input(
                    "聚合数据 URL",
                    value=DEFAULT_AGGREGATOR_URL,
                )
            elif mode_label == "指定岗位 URL":
                seed_url_text = st.text_area("岗位 URL", height=100)
            st.markdown('<div class="ui-panel-label">运行</div>', unsafe_allow_html=True)
            submitted = st.form_submit_button(
                "开始运行岗位 Agent",
                type="primary",
                width="stretch",
            )

    run_error: str | None = None
    if submitted:
        temporary_path: Path | None = None
        try:
            combined_resume = resume_text.strip()
            if resume_upload is not None:
                uploaded_resume = extract_resume_upload(
                    resume_upload.getvalue(),
                    filename=resume_upload.name,
                )
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
                    "实时岗位（官方 API）": "official",
                    "聚合岗位（上传/URL）": "aggregator",
                    "指定岗位 URL": "seed_urls",
                }[mode_label or "内置 Demo"],
                aggregator_path=str(temporary_path) if temporary_path else None,
                aggregator_url=aggregator_url_text.strip() or None,
                official_specs=official_specs or None,
                seed_urls=parse_seed_urls(seed_url_text),
                llm_extractor_provider=extractor_provider,
                llm_match_provider=match_provider,
            )
            if should_consume_llm_budget(request):
                consume_llm_budget(
                    st.session_state.setdefault(f"llm_budget-{owner_id}", {})
                )
            with st.spinner("Agent 正在运行..."):
                st.session_state["latest_ui_result"] = asyncio.run(
                    run_ui_request(
                        request,
                        environ=os.environ,
                        run_store=run_store,
                        artifact_store=artifact_store,
                        owner_id=owner_id,
                    )
                )
                st.session_state["latest_ui_owner"] = owner_id
        except (UiRequestError, ValueError, OSError) as exc:
            run_error = str(exc)
        except Exception as exc:  # noqa: BLE001
            run_error = f"任务运行失败：{type(exc).__name__}: {exc}"
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    result = st.session_state.get("latest_ui_result")
    latest_owner = st.session_state.get("latest_ui_owner")
    if submitted and isinstance(result, UiRunResult) and latest_owner == owner_id:
        view_run_id = result.run_id
    else:
        view_run_id = selected_run_id
        if view_run_id and (
            not isinstance(result, UiRunResult)
            or result.run_id != view_run_id
            or latest_owner != owner_id
        ):
            result = load_persisted_ui_result(
                run_store,
                artifact_store,
                owner_id,
                view_run_id,
            )
        elif view_run_id is None and latest_owner != owner_id:
            result = None

    with header_slot.container():
        st.markdown(
            brand_header_html(
                result if isinstance(result, UiRunResult) else None,
                data_mode=mode_key,
            ),
            unsafe_allow_html=True,
        )

    with result_column:
        st.markdown('<div class="ui-result-shell"></div>', unsafe_allow_html=True)
        if run_error:
            st.error(run_error)
        elif isinstance(result, UiRunResult):
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
                st.info(
                    f"运行 {record.run_id} 当前状态："
                    f"{RUN_STATUS_LABELS.get(record.status, record.status)}"
                )
        else:
            st.markdown(workspace_empty_state_html(), unsafe_allow_html=True)


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
            st.success(diagnostics_empty_message())
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
