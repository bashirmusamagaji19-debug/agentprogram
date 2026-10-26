from contextlib import nullcontext

from web_task_agent.models import JobPosting, RunMetrics
from web_task_agent.streamlit_app import (
    _render_result,
    body_source_text,
    job_cards_html,
    job_result_rows,
    link_verification_text,
    pending_job_rows,
    pending_jobs_html,
    run_status,
    saved_status_text,
)
from web_task_agent.streamlit_runner import UiRunResult


def make_job(url="https://example.com/jobs/1", **overrides):
    data = {
        "title": "Agent Intern",
        "company": "Example AI",
        "location": "Remote",
        "source": "fixture",
        "url": url,
    }
    data.update(overrides)
    return JobPosting(**data)


def receipt(status="verified", **overrides):
    data = {
        "status": status,
        "checked_at": "2026-10-26T08:30:00+00:00",
        "run_id": "run-link-1",
        "requested_url": "https://example.com/jobs/redirect",
        "final_url": "https://example.com/jobs/1",
        "method": "GET",
        "reason": "title and JD matched",
        "evidence_sha256": "a" * 64,
        "title_matched": True,
        "jd_matched": True,
        "active": True,
    }
    data.update(overrides)
    return data


def result_with(jobs, pending_jobs=None):
    return UiRunResult.model_validate(
        {
            "run_id": "run-link-1",
            "jobs": jobs,
            "pending_jobs": pending_jobs or [],
            "metrics": RunMetrics(run_id="run-link-1", valid_jobs=len(jobs)),
        }
    )


def test_body_source_and_page_link_receipt_are_separate_claims():
    job = make_job().model_copy(
        update={
            "url_verification": "api-verified",
            "link_verification": receipt(),
        }
    )

    assert "正文来源" in body_source_text(job)
    assert "官方" in body_source_text(job)
    assert "页面" not in body_source_text(job)
    assert link_verification_text(job) == "✓ 已核验"
    assert body_source_text(job) != link_verification_text(job)


def test_real_legacy_history_is_not_presented_as_current_verified_delivery():
    result = result_with([make_job()]).model_copy(update={"data_mode": "official", "run_status": "succeeded"})
    title, explanation, tone = run_status(result)
    assert tone == "warning"
    assert "历史" in title and "重新运行" in explanation


def test_delivered_rows_show_receipt_time_method_and_final_url():
    job = make_job().model_copy(
        update={
            "url_verification": "api-verified",
            "link_verification": receipt(),
        }
    )

    row = job_result_rows(result_with([job]))[0]

    assert row["正文来源"] == "官方详情 API（正文来源）"
    assert row["链接核验"] == "✓ 已核验"
    assert row["核验时间"] == "2026-10-26T08:30:00+00:00"
    assert row["核验方式"] == "GET"
    assert row["最终URL"] == "https://example.com/jobs/1"


def test_legacy_job_without_receipt_is_not_reported_as_web_verified():
    job = make_job().model_copy(
        update={"url_verification": "api-verified", "link_verification": {}}
    )

    row = job_result_rows(result_with([job]))[0]

    assert row["正文来源"] == "官方详情 API（正文来源）"
    assert row["链接核验"] == "未核验（无核验记录）"
    assert row["核验时间"] == "—"
    assert row["核验方式"] == "—"
    assert row["最终URL"] == "—"
    assert "网页已验证" not in job_cards_html(result_with([job]))


def test_pending_jobs_have_separate_rows_reason_and_no_main_table_entry():
    pending = make_job(url="https://example.com/jobs/pending").model_copy(
        update={
            "link_verification": receipt(
                status="pending",
                reason="page title did not match",
                final_url="",
                title_matched=False,
                jd_matched=False,
                active=False,
            )
        }
    )
    result = result_with([], [pending])

    assert job_result_rows(result) == []
    assert job_cards_html(result) == ""
    rows = pending_job_rows(result)
    assert len(rows) == 1
    assert rows[0]["岗位"] == "Agent Intern"
    assert rows[0]["状态"] == "待核验"
    assert rows[0]["未交付原因"] == "page title did not match"

    html = pending_jobs_html(result)
    assert "未交付候选" in html
    assert "0 个交付" in html
    assert "page title did not match" in html
    assert "ui-pending" in html
    assert "ui-metric-success" not in html
    assert run_status(result)[2] == "warning"
    assert "未交付候选未保存" in saved_status_text(
        result.model_copy(update={"saved": True})
    )


def test_duplicate_pending_url_is_excluded_from_delivered_views():
    pending = make_job().model_copy(
        update={"link_verification": receipt(status="pending", reason="awaiting check")}
    )
    result = result_with([pending], [pending])

    assert job_result_rows(result) == []
    assert job_cards_html(result) == ""


def test_zero_delivery_result_renders_pending_area_before_empty_main_table():
    pending = make_job(url="https://example.com/jobs/pending").model_copy(
        update={
            "link_verification": receipt(
                status="pending",
                reason="active status unknown",
                final_url="",
                active=None,
            )
        }
    )

    class RecordingStreamlit:
        def __init__(self):
            self.markdown_calls: list[str] = []

        def markdown(self, body, **_kwargs):
            self.markdown_calls.append(str(body))

        def tabs(self, _labels):
            return [nullcontext() for _ in range(4)]

        def expander(self, *_args, **_kwargs):
            return nullcontext()

        def caption(self, *_args, **_kwargs):
            return None

        def warning(self, *_args, **_kwargs):
            return None

        def info(self, *_args, **_kwargs):
            return None

        def success(self, *_args, **_kwargs):
            return None

        def dataframe(self, *_args, **_kwargs):
            return None

    st = RecordingStreamlit()
    _render_result(st, result_with([], [pending]))

    pending_markdown = [body for body in st.markdown_calls if "未交付候选" in body]
    assert len(pending_markdown) == 1
    assert "0 个交付" in pending_markdown[0]
    assert "active status unknown" in pending_markdown[0]
    assert not any("表格视图" in body for body in st.markdown_calls)
