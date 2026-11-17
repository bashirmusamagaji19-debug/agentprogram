from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from web_task_agent.models import JobPosting, MatchResult, RunMetrics
from web_task_agent.streamlit_app import (
    artifact_cards_html,
    artifact_download_spec,
    diagnostic_rows,
    empty_result_explanation,
    humanize_terminal_reason,
    internship_evidence_text,
    job_cards_html,
    job_result_rows,
    metric_grid_html,
    mode_hint,
    progress_summary_text,
    run_status,
    saved_status_text,
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
        diagnostics=[{"url": "https://example.com/jobs/2", "error": "empty_page: no text"}],
        execution_trace=[{"node": "planner", "summary": "planned 3 queries"}],
        artifacts={"report": report},
    )


def test_job_result_rows_join_jobs_with_match_results(tmp_path: Path) -> None:
    rows = job_result_rows(make_result(tmp_path))

    assert rows == [
        {
            "岗位": "AI Agent 实习生",
            "公司": "示例科技",
            "正文来源": "来源未标注",
            "链接核验": "未核验（无核验记录）",
            "核验时间": "—",
            "核验方式": "—",
            "最终URL": "—",
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


def test_run_status_does_not_claim_pending_or_rejected_saved(tmp_path):
    pending = make_result(tmp_path).model_copy(update={"run_status": "awaiting_approval"})
    assert run_status(pending)[0] == "等待确认"
    rejected = pending.model_copy(update={"run_status": "rejected"})
    assert run_status(rejected)[0] == "已拒绝"
    partial = pending.model_copy(update={"run_status": "partial", "terminal_reason": "candidates_exhausted"})
    assert run_status(partial)[0] == "部分完成"


def test_run_status_distinguishes_ready_success_and_partial_failure(tmp_path: Path) -> None:
    assert run_status(None) == ("待运行", "配置任务后开始一次岗位扫描", "ready")
    assert run_status(make_result(tmp_path)) == ("已完成", "1 个有效岗位 · 1 个页面失败", "warning")


def test_search_condition_exclusions_are_not_page_failures(tmp_path):
    result = make_result(tmp_path)
    result.metrics.failed_pages = 0
    result.diagnostics = [{"url": "https://example.com/jobs/2",
                           "error": "verification_filtered: location mismatch: requested 北京, got 上海",
                           "kind": "search_filter"}]
    assert run_status(result)[2] == "success"
    assert "页面失败" not in run_status(result)[1]


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


def test_cloud_requirements_cover_live_link_verification_runtime() -> None:
    lines = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    names = {
        part.strip().lower()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
        for part in [line.split(";")[0].split(">=")[0].split("<")[0].split("==")[0]]
    }

    assert {"streamlit", "langgraph", "pydantic", "python-dotenv"} <= names
    assert "browser-use" in names
    assert "browser-use==0.12.9" in lines


def test_cloud_system_packages_provide_chromium() -> None:
    package_file = REPO_ROOT / "packages.txt"
    assert package_file.is_file(), "Cloud link verification requires a system browser"
    packages = package_file.read_text(encoding="ascii").splitlines()

    assert "chromium" in packages


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
        st,
        "secrets",
        dict.fromkeys(PROVIDER_API_KEY_ENV.values(), "sk-from-secrets"),
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


# ── 公开演示的成本保护:LLM 会话限流 ──


def test_llm_run_budget_allows_then_blocks():
    """公开演示定位:任何人可体验,但 LLM 额度有限 —
    每会话限 3 次 LLM 运行,超出后拒绝并提示。"""
    from web_task_agent.streamlit_app import consume_llm_budget, llm_budget_remaining

    budget: dict = {}
    assert llm_budget_remaining(budget) == 3

    consume_llm_budget(budget)
    consume_llm_budget(budget)
    assert llm_budget_remaining(budget) == 1

    consume_llm_budget(budget)
    assert llm_budget_remaining(budget) == 0

    import pytest

    from web_task_agent.streamlit_runner import UiRequestError

    with pytest.raises(UiRequestError, match="LLM"):
        consume_llm_budget(budget)  # 超限必须拒绝,不能负数


def test_real_provider_consumes_llm_budget_even_on_demo_data():
    """演示数据配真实模型仍有请求成本。"""
    from web_task_agent.streamlit_app import consume_llm_budget, should_consume_llm_budget

    budget: dict = {}
    request = UiRunRequest(data_mode="demo", llm_match_provider="qwen")

    assert should_consume_llm_budget(request)
    assert not should_consume_llm_budget(UiRunRequest(data_mode="demo"))

    request = UiRunRequest(
        data_mode="official", official_specs=["tencent-campus"], llm_match_provider="qwen"
    )
    assert should_consume_llm_budget(request)
    consume_llm_budget(budget)


def test_default_ui_uses_xiaomi_three_targets_and_empty_skills(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(
        str(REPO_ROOT / "streamlit_app.py"), default_timeout=30
    ).run()

    assert not app.exception
    mode = next(item for item in app.segmented_control if item.label == "数据模式")
    assert mode.value == "实时岗位（官方 API）"
    sources = next(item for item in app.multiselect if item.label == "官方发现源")
    assert sources.value == ["xiaomi"]
    target = next(item for item in app.number_input if item.label == "目标岗位数")
    assert target.value == 3
    skills = next(item for item in app.text_input if item.label == "技能标签")
    assert skills.value == ""
    toggles = {item.label: item.value for item in app.toggle}
    assert toggles["启用 LLM 抽取"] is False
    assert toggles["启用 LLM 匹配"] is False
    assert toggles["启用模型选择候选"] is False


def test_model_selectors_only_offer_configured_providers(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    app = AppTest.from_file(str(REPO_ROOT / "streamlit_app.py"), default_timeout=30).run()
    for toggle in app.toggle:
        if toggle.label in {"启用 LLM 抽取", "启用 LLM 匹配", "启用模型选择候选"}:
            toggle.set_value(True)
    app.run()

    assert not app.exception
    for label in ["抽取模型", "匹配模型", "规划模型"]:
        selector = next(item for item in app.selectbox if item.label == label)
        assert selector.options == ["deepseek"]
        assert selector.value == "deepseek"


def test_model_toggles_are_disabled_without_credentials(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    app = AppTest.from_file(str(REPO_ROOT / "streamlit_app.py"), default_timeout=30).run()

    assert not app.exception
    model_toggles = [t for t in app.toggle if t.label in {
        "启用 LLM 抽取", "启用 LLM 匹配", "启用模型选择候选",
    }]
    assert len(model_toggles) == 3
    assert all(toggle.disabled for toggle in model_toggles)


def test_health_button_runs_synthetic_probe_and_shows_both_results(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from web_task_agent import streamlit_app as ui

    calls = []

    def probe():
        calls.append(True)
        return {"text": {"ok": True, "elapsed_seconds": 0.5},
                "vision": {"ok": True, "elapsed_seconds": 0.8}}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(ui, "check_deepseek_capabilities", probe)
    app = AppTest.from_file(str(REPO_ROOT / "streamlit_app.py"), default_timeout=30).run()
    next(b for b in app.button if b.label == "检测 DeepSeek 文本与视觉").click().run()

    assert not app.exception
    assert calls == [True]
    assert any("文本推理通过" in item.value for item in app.success)
    assert any("图片识别通过" in item.value for item in app.success)


@pytest.mark.parametrize("target_count, expected_deadline", [(1, 90), (3, 150), (6, 240), (50, 360)])
def test_ui_run_deadline_scales_with_target_count(tmp_path, monkeypatch, target_count, expected_deadline):
    from streamlit.testing.v1 import AppTest

    from web_task_agent import streamlit_app as ui

    captured = []

    async def capture_request(request, **kwargs):
        captured.append(request)
        raise UiRequestError("test request captured")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ui, "run_ui_request", capture_request)
    app = AppTest.from_file(str(REPO_ROOT / "streamlit_app.py"), default_timeout=30).run()
    next(item for item in app.number_input if item.label == "目标岗位数").set_value(target_count)
    next(item for item in app.button if item.label == "开始运行岗位 Agent").click().run()

    assert not app.exception
    assert len(captured) == 1
    assert captured[0].deadline_seconds == expected_deadline


def test_job_cards_show_internship_evidence_basis_and_low_score(tmp_path: Path) -> None:
    result = make_result(tmp_path)
    result.jobs[0] = result.jobs[0].model_copy(
        update={
            "internship_evidence_source": "xiaomi:list:type",
            "internship_evidence_value": "3",
        }
    )
    result.matches[0] = result.matches[0].model_copy(
        update={"score": 0.3, "reason": "命中 Python，缺少 RAG"}
    )

    html = job_cards_html(result)

    assert "AI Agent 实习生" in html
    assert "示例科技" in html
    assert "北京" in html
    assert "官方实习" in html
    assert "type=3" in html
    assert "https://example.com/jobs/1" in html
    assert "Python" in html and "RAG" in html
    assert "命中 Python，缺少 RAG" in html
    assert "30%" in html


def test_internship_evidence_uses_verified_xiaomi_enum_and_title_fallback() -> None:
    detail = JobPosting(
        title="AI 算法工程师",
        company="小米",
        location="北京",
        source="xiaomi",
        url="https://example.com/xiaomi/1",
        internship_evidence_source="xiaomi:detail:recruit_type.id",
        internship_evidence_value="202",
    )
    title_only = JobPosting(
        title="Agent 实习",
        company="示例",
        location="上海",
        source="fixture",
        url="https://example.com/jobs/2",
    )
    unknown = title_only.model_copy(update={"title": "Agent 工程师"})

    assert "官方实习" in internship_evidence_text(detail)
    assert "202" in internship_evidence_text(detail)
    assert "标题含“实习”" in internship_evidence_text(title_only)
    assert internship_evidence_text(unknown) == "未提供"


def test_terminal_reasons_are_user_readable() -> None:
    assert humanize_terminal_reason("budget_exhausted") == (
        "本轮检查步数已用完，已交付确认的部分岗位"
    )
    assert humanize_terminal_reason("deadline_exceeded") == (
        "达到本次总时限，已停止处理"
    )
    for reason in ("candidates_exhausted", "no_candidates", "search_error"):
        readable = humanize_terminal_reason(reason)
        assert readable
        assert reason not in readable


def test_partial_status_hides_internal_terminal_code(tmp_path: Path) -> None:
    result = make_result(tmp_path).model_copy(
        update={"run_status": "partial", "terminal_reason": "budget_exhausted"}
    )

    status, detail, _ = run_status(result)

    assert status == "部分完成"
    assert "budget_exhausted" not in detail
    assert "检查步数" in detail


def test_progress_summary_reports_counts_and_unexamined_candidates(
    tmp_path: Path,
) -> None:
    result = make_result(tmp_path).model_copy(
        update={
            "progress": {
                "discovered": 12,
                "selected": 10,
                "checked": 7,
                "verified": 2,
                "delivered": 2,
                "unexamined": 5,
                "limit": 10,
            }
        }
    )

    summary = progress_summary_text(result)

    assert "发现 12" in summary
    assert "选定 10" in summary
    assert "已检查 7" in summary
    assert "验证通过 2" in summary
    assert "已交付 2" in summary
    assert "未检查 5" in summary
    assert "上限 10" in summary


def test_empty_result_explains_counts_filters_and_remaining_candidates(
    tmp_path: Path,
) -> None:
    result = make_result(tmp_path)
    result.jobs = []
    result.matches = []
    result.metrics.valid_jobs = 0
    result.diagnostics = [
        {
            "url": "https://example.com/jobs/2",
            "error": "search_filtered: location mismatch",
            "kind": "search_filter",
        },
        {
            "url": "https://example.com/jobs/3",
            "error": "verification_filtered: missing internship evidence",
            "kind": "verification",
        },
    ]
    result.progress = {
        "discovered": 12,
        "selected": 10,
        "checked": 7,
        "verified": 0,
        "delivered": 0,
        "unexamined": 2,
        "limit": 10,
    }

    explanation = empty_result_explanation(result)

    assert "发现 12" in explanation
    assert "已检查 7" in explanation
    assert "关键词或地点过滤 1" in explanation
    assert "正文验证过滤 1" in explanation
    assert "还有 2 条候选未检查" in explanation
    assert "上限 10" in explanation


def test_saved_status_distinguishes_pending_and_saved(tmp_path: Path) -> None:
    pending = make_result(tmp_path).model_copy(update={"run_status": "awaiting_approval"})
    saved = make_result(tmp_path).model_copy(update={"saved": True})

    assert "尚未保存" in saved_status_text(pending)
    assert "已保存到岗位库" in saved_status_text(saved)


def test_empty_explanation_does_not_treat_status_word_as_reason(tmp_path: Path) -> None:
    result = make_result(tmp_path)
    result.jobs = []
    result.matches = []
    result.terminal_status = "partial"
    result.terminal_reason = ""

    explanation = empty_result_explanation(result)

    assert "运行提前结束" not in explanation


def test_diagnostic_budget_code_is_rendered_as_user_explanation() -> None:
    result = UiRunResult(
        run_id="run-ui",
        metrics=RunMetrics(run_id="run-ui"),
        diagnostics=[{"url": "https://example.com/jobs/1", "error": "budget_exhausted"}],
    )

    row = diagnostic_rows(result)[0]

    assert row["类别"] == "本轮检查步数已用完"
    assert "budget_exhausted" not in row["详情"]
