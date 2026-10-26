from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from web_task_agent.browser import BrowserPage, BrowserUseClient, PageHttpError
from web_task_agent.link_verification import JobLinkVerifier, is_deliverable

JOB_URL = "https://jobs.example.com/positions/123"
JOB_TITLE = "AI Platform Engineer"
JOB_JD = (
    "Build reliable Python services for large language model evaluation and deployment. "
    "Own observability, incident response, and production quality."
)


def make_job(url: str = JOB_URL) -> SimpleNamespace:
    return SimpleNamespace(
        title=JOB_TITLE,
        company="Example Labs",
        location="Shanghai",
        source="official",
        url=url,
        requirements="Python, production services, and observability",
        responsibilities=JOB_JD,
        skills=["Python", "LLM", "observability"],
    )


def make_page(
    *,
    url: str = JOB_URL,
    final_url: str | None = None,
    title: str = JOB_TITLE,
    content: str | None = None,
    status_code: int = 200,
    apply_text: str | None = "Apply now",
    apply_visible: bool = True,
    apply_enabled: bool = True,
) -> BrowserPage:
    controls = []
    if apply_text is not None:
        controls.append(
            {
                "tag": "button",
                "text": apply_text,
                "visible": apply_visible,
                "enabled": apply_enabled,
            }
        )
    return BrowserPage(
        url=final_url or url,
        title=title,
        content=content if content is not None else f"{JOB_TITLE}\n{company_line()}\n{JOB_JD}",
        source="http",
        metadata={
            "requested_url": url,
            "final_url": final_url or url,
            "final_url_observed": True,
            "status_code": status_code,
            "apply_controls": controls,
        },
    )


def company_line() -> str:
    return "Example Labs - Shanghai"


def assert_receipt_shape(receipt: dict[str, object]) -> None:
    assert set(receipt) == {
        "status",
        "checked_at",
        "run_id",
        "requested_url",
        "final_url",
        "method",
        "reason",
        "evidence_sha256",
        "title_matched",
        "jd_matched",
        "active",
    }
    assert receipt["status"] in {"verified", "pending", "rejected"}
    checked_at = datetime.fromisoformat(str(receipt["checked_at"]))
    assert checked_at.tzinfo is not None
    assert receipt["method"] in {"http", "browser"}
    assert len(str(receipt["evidence_sha256"])) == 64
    assert all(isinstance(receipt[key], bool) for key in ("title_matched", "jd_matched", "active"))


@pytest.mark.asyncio
async def test_http_candidate_requires_browser_confirmation_for_visible_apply_button():
    http_calls: list[str] = []
    browser_calls: list[str] = []

    async def http_loader(url: str) -> BrowserPage:
        http_calls.append(url)
        return make_page(url=url, final_url=f"{url}?source=careers")

    async def browser_loader(url: str) -> BrowserPage:
        browser_calls.append(url)
        return make_page(url=url)

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        browser_loader=browser_loader,
    ).verify(make_job(), run_id="run-1")

    assert_receipt_shape(receipt)
    assert receipt["status"] == "verified"
    assert receipt["method"] == "browser"
    assert receipt["requested_url"] == JOB_URL
    assert receipt["final_url"] == JOB_URL
    assert receipt["title_matched"] is True
    assert receipt["jd_matched"] is True
    assert receipt["active"] is True
    assert http_calls == [JOB_URL]
    assert browser_calls == [JOB_URL]


@pytest.mark.asyncio
async def test_http_plain_button_without_rendered_browser_is_only_pending():
    calls: list[str] = []

    async def http_loader(url: str) -> BrowserPage:
        calls.append(url)
        return make_page(url=url)

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        enable_browser_fallback=False,
    ).verify(make_job(), run_id="run-1")

    assert receipt["status"] == "pending"
    assert receipt["method"] == "http"
    assert receipt["title_matched"] is True
    assert receipt["jd_matched"] is True
    assert receipt["active"] is False
    assert "browser" in receipt["reason"].lower()
    assert calls == [JOB_URL]


@pytest.mark.asyncio
async def test_unobserved_http_status_keeps_none_in_retained_evidence(tmp_path):
    async def http_loader(url: str) -> BrowserPage:
        page = make_page(url=url)
        page.metadata.pop("status_code")
        return page

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        enable_browser_fallback=False,
        evidence_dir=tmp_path / "link-checks",
    ).verify(make_job(), run_id="run-1")

    payload = json.loads(
        (tmp_path / "link-checks" / f"{receipt['evidence_sha256']}.json").read_text(encoding="utf-8")
    )
    assert receipt["status"] == "pending"
    assert payload["status_code"] is None
    assert "status" in receipt["reason"].lower()


@pytest.mark.asyncio
async def test_browser_evidence_keeps_unobserved_status_as_none(tmp_path):
    async def http_loader(url: str) -> BrowserPage:
        raise PageHttpError("HTTP 404 from static fetch", status_code=404)

    async def browser_loader(url: str) -> BrowserPage:
        page = make_page(url=url)
        page.metadata.pop("status_code")
        return page

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        browser_loader=browser_loader,
        evidence_dir=tmp_path / "link-checks",
    ).verify(make_job(), run_id="run-1")

    payload = json.loads(
        (tmp_path / "link-checks" / f"{receipt['evidence_sha256']}.json").read_text(encoding="utf-8")
    )
    assert receipt["status"] == "verified"
    assert receipt["method"] == "browser"
    assert payload["browser"]["status_code"] is None


@pytest.mark.asyncio
async def test_each_verify_performs_a_fresh_http_visit():
    calls: list[str] = []

    async def http_loader(url: str) -> BrowserPage:
        calls.append(url)
        return make_page(url=url)

    verifier = JobLinkVerifier(
        http_loader=http_loader,
        enable_browser_fallback=False,
    )
    await verifier.verify(make_job(), run_id="run-1")
    await verifier.verify(make_job(), run_id="run-2")

    assert calls == [JOB_URL, JOB_URL]


@pytest.mark.asyncio
async def test_configured_evidence_directory_retains_the_hashed_public_payload(tmp_path):
    async def http_loader(url: str) -> BrowserPage:
        return make_page(url=url)

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        enable_browser_fallback=False,
        evidence_dir=tmp_path / "link-checks",
    ).verify(make_job(), run_id="run-1")

    evidence_path = tmp_path / "link-checks" / f"{receipt['evidence_sha256']}.json"
    encoded = evidence_path.read_bytes()
    payload = json.loads(encoded)
    assert hashlib.sha256(encoded).hexdigest() == receipt["evidence_sha256"]
    assert payload["requested_url"] == JOB_URL
    assert payload["final_url"] == JOB_URL
    assert payload["title"] == JOB_TITLE
    assert JOB_JD in payload["content"]
    assert payload["apply_controls"][0]["text"] == "Apply now"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [404, 410])
async def test_http_gone_status_is_rejected(status_code: int):
    async def http_loader(url: str) -> BrowserPage:
        raise PageHttpError("gone", status_code=status_code)

    browser_calls: list[str] = []

    async def browser_loader(url: str) -> BrowserPage:
        browser_calls.append(url)
        return make_page(url=url)

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        browser_loader=browser_loader,
        enable_browser_fallback=False,
    ).verify(make_job(), run_id="run-1")

    assert receipt["status"] == "rejected"
    assert receipt["method"] == "http"
    assert str(status_code) in receipt["reason"]
    assert browser_calls == []


@pytest.mark.asyncio
async def test_http_404_falls_back_to_browser_and_verifies_rendered_detail():
    async def http_loader(url: str) -> BrowserPage:
        raise PageHttpError("HTTP 404 from static fetch", status_code=404)

    browser_calls: list[str] = []

    async def browser_loader(url: str) -> BrowserPage:
        browser_calls.append(url)
        return make_page(url=url)

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        browser_loader=browser_loader,
    ).verify(make_job(), run_id="run-1")

    assert receipt["status"] == "verified"
    assert receipt["method"] == "browser"
    assert receipt["title_matched"] is True
    assert receipt["jd_matched"] is True
    assert receipt["active"] is True
    assert browser_calls == [JOB_URL]


@pytest.mark.asyncio
async def test_browser_rendered_detail_can_verify_even_when_browser_metadata_reports_404():
    async def http_loader(url: str) -> BrowserPage:
        raise PageHttpError("HTTP 404 from static fetch", status_code=404)

    async def browser_loader(url: str) -> BrowserPage:
        return make_page(url=url, status_code=404)

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        browser_loader=browser_loader,
    ).verify(make_job(), run_id="run-1")

    assert receipt["status"] == "verified"
    assert receipt["method"] == "browser"
    assert receipt["active"] is True


@pytest.mark.asyncio
async def test_http_404_then_browser_tombstone_is_rejected():
    async def http_loader(url: str) -> BrowserPage:
        raise PageHttpError("HTTP 404 from static fetch", status_code=404)

    async def browser_loader(url: str) -> BrowserPage:
        return make_page(
            url=url,
            content=f"{JOB_TITLE}\n{company_line()}\n该岗位已停止招聘",
        )

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        browser_loader=browser_loader,
    ).verify(make_job(), run_id="run-1")

    assert receipt["status"] == "rejected"
    assert receipt["method"] == "browser"
    assert receipt["active"] is False


@pytest.mark.asyncio
async def test_http_404_browser_failure_is_pending_and_keeps_404_evidence():
    async def http_loader(url: str) -> BrowserPage:
        raise PageHttpError("HTTP 404 from static fetch", status_code=404)

    async def browser_loader(url: str) -> BrowserPage:
        raise RuntimeError("renderer unavailable")

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        browser_loader=browser_loader,
    ).verify(make_job(), run_id="run-1")

    assert receipt["status"] == "pending"
    assert receipt["method"] == "browser"
    assert "404" in receipt["reason"]
    assert receipt["title_matched"] is False
    assert receipt["active"] is False


@pytest.mark.asyncio
async def test_http_page_with_404_status_also_uses_browser_confirmation():
    async def http_loader(url: str) -> BrowserPage:
        return make_page(url=url, status_code=404, content="Not Found")

    async def browser_loader(url: str) -> BrowserPage:
        return make_page(url=url)

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        browser_loader=browser_loader,
    ).verify(make_job(), run_id="run-1")

    assert receipt["status"] == "verified"
    assert receipt["method"] == "browser"


@pytest.mark.asyncio
async def test_explicit_offline_copy_is_rejected_even_with_apply_button():
    async def http_loader(url: str) -> BrowserPage:
        return make_page(
            url=url,
            title="AI Platform Engineer",
            content=f"{JOB_TITLE}\n{company_line()}\n此岗位已停止招聘",
        )

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        enable_browser_fallback=False,
    ).verify(make_job(), run_id="run-1")

    assert receipt["status"] == "rejected"
    assert receipt["active"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "Please sign in to continue to this job.",
        "Verify you are human before continuing.",
        "Loading...",
    ],
)
async def test_challenge_shell_redirects_to_rendered_browser_and_uses_actual_url(content: str):
    rendered_url = "https://jobs.example.com/job/123?lang=en"
    browser_calls: list[str] = []

    async def http_loader(url: str) -> BrowserPage:
        return make_page(url=url, content=content, apply_text=None)

    async def browser_loader(url: str) -> BrowserPage:
        browser_calls.append(url)
        return make_page(
            url=rendered_url,
            final_url=rendered_url,
            title=JOB_TITLE,
            content=f"{JOB_TITLE}\n{company_line()}\n{JOB_JD}",
        )

    receipt = await JobLinkVerifier(
        http_loader=http_loader, browser_loader=browser_loader
    ).verify(make_job(), run_id="run-1")

    assert receipt["status"] == "verified"
    assert receipt["method"] == "browser"
    assert receipt["final_url"] == rendered_url
    assert browser_calls == [JOB_URL]


@pytest.mark.asyncio
async def test_spa_shell_without_browser_is_pending():
    async def http_loader(url: str) -> BrowserPage:
        return make_page(url=url, content="Loading...", apply_text=None)

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        enable_browser_fallback=False,
    ).verify(make_job(), run_id="run-1")

    assert receipt["status"] == "pending"
    assert "browser" in receipt["reason"].lower()


@pytest.mark.asyncio
async def test_matching_title_without_jd_snippet_is_only_pending():
    async def http_loader(url: str) -> BrowserPage:
        return make_page(url=url, content=f"{JOB_TITLE}\n{company_line()}\nApply now")

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        enable_browser_fallback=False,
    ).verify(make_job(), run_id="run-1")

    assert receipt["status"] == "pending"
    assert receipt["title_matched"] is True
    assert receipt["jd_matched"] is False
    assert receipt["active"] is False


@pytest.mark.asyncio
async def test_job_duty_words_about_closing_or_finishing_do_not_mean_offline():
    duties = (
        f"{JOB_JD} Lead incident reviews after a production incident is closed, "
        "and report lessons learned when an initiative finishes."
    )

    async def http_loader(url: str) -> BrowserPage:
        return make_page(
            url=url,
            content=f"{JOB_TITLE}\n{company_line()}\n{duties}",
        )

    job = make_job()
    job.responsibilities = duties
    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        enable_browser_fallback=False,
    ).verify(job, run_id="run-1")

    assert receipt["status"] == "pending"
    assert receipt["jd_matched"] is True
    assert receipt["active"] is False
    assert "browser" in receipt["reason"].lower()


@pytest.mark.asyncio
async def test_chinese_duty_words_about_closing_or_finishing_do_not_mean_offline():
    duties = (
        f"{JOB_JD} 负责活动关闭后的复盘，项目结束时提交交付报告，"
        "在岗位关闭后完成归档。"
    )

    async def http_loader(url: str) -> BrowserPage:
        return make_page(
            url=url,
            content=f"{JOB_TITLE}\n{company_line()}\n{duties}",
        )

    job = make_job()
    job.responsibilities = duties
    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        enable_browser_fallback=False,
    ).verify(job, run_id="run-1")

    assert receipt["status"] == "pending"
    assert receipt["jd_matched"] is True
    assert receipt["active"] is False
    assert "browser" in receipt["reason"].lower()


@pytest.mark.asyncio
async def test_disabled_or_hidden_apply_control_does_not_establish_active_job():
    async def http_loader(url: str) -> BrowserPage:
        return make_page(url=url, apply_visible=False, apply_enabled=False)

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        enable_browser_fallback=False,
    ).verify(make_job(), run_id="run-1")

    assert receipt["status"] == "pending"
    assert receipt["active"] is False


@pytest.mark.asyncio
async def test_apply_words_in_page_text_do_not_establish_active_without_a_control():
    async def http_loader(url: str) -> BrowserPage:
        return make_page(
            url=url,
            content=f"{JOB_TITLE}\n{company_line()}\n{JOB_JD}\nApply now",
            apply_text=None,
        )

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        enable_browser_fallback=False,
    ).verify(make_job(), run_id="run-1")

    assert receipt["status"] == "pending"
    assert receipt["active"] is False


@pytest.mark.asyncio
async def test_matching_title_jd_and_cta_still_pend_when_actual_url_was_not_observed():
    async def http_loader(url: str) -> BrowserPage:
        page = make_page(url=url)
        page.metadata["final_url_observed"] = False
        return page

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        enable_browser_fallback=False,
    ).verify(make_job(), run_id="run-1")

    assert receipt["status"] == "pending"
    assert receipt["title_matched"] is False
    assert receipt["jd_matched"] is False
    assert receipt["active"] is False
    assert "URL" in receipt["reason"]


@pytest.mark.asyncio
async def test_redirect_to_foreign_host_without_preserved_native_id_is_pending():
    async def http_loader(url: str) -> BrowserPage:
        return make_page(url=url, final_url="https://login.example.net/home")

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        enable_browser_fallback=False,
    ).verify(make_job(), run_id="run-1")

    assert receipt["status"] == "pending"
    assert "redirect" in receipt["reason"].lower()


@pytest.mark.asyncio
async def test_detail_page_navigation_with_all_jobs_text_does_not_make_it_a_list_page():
    async def http_loader(url: str) -> BrowserPage:
        return make_page(
            url=url,
            content=(
                f"全部职位\n{JOB_TITLE}\n{company_line()}\n{JOB_JD}\nApply now"
            ),
        )

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        enable_browser_fallback=False,
    ).verify(make_job(), run_id="run-1")

    assert receipt["status"] == "pending"
    assert receipt["title_matched"] is True
    assert receipt["jd_matched"] is True
    assert receipt["active"] is False
    assert "browser" in receipt["reason"].lower()


@pytest.mark.asyncio
async def test_redirect_to_foreign_host_with_preserved_native_id_is_pending():
    requested = "https://jobs.example.com/apply?id=abc987"
    final = "https://careers.example.org/positions/abc987"

    async def http_loader(url: str) -> BrowserPage:
        return make_page(url=url, final_url=final)

    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        enable_browser_fallback=False,
    ).verify(
        make_job(requested), run_id="run-1"
    )

    assert receipt["status"] == "pending"
    assert receipt["final_url"] == final
    assert "redirect" in receipt["reason"].lower()


def test_production_default_constructs_the_browser_renderer_without_opening_it():
    verifier = JobLinkVerifier()

    assert isinstance(verifier.browser_loader, BrowserUseClient)
    assert verifier.browser_loader.render_wait_seconds == 5.0


def test_browser_fallback_can_be_disabled_explicitly():
    async def http_loader(url: str) -> BrowserPage:
        return make_page(url=url)

    verifier = JobLinkVerifier(
        http_loader=http_loader,
        enable_browser_fallback=False,
    )

    assert verifier.browser_loader is None


@pytest.mark.asyncio
async def test_total_deadline_bounds_a_hanging_loader():
    async def http_loader(url: str) -> BrowserPage:
        await asyncio.sleep(10)
        return make_page(url=url)

    started = asyncio.get_running_loop().time()
    receipt = await JobLinkVerifier(
        http_loader=http_loader,
        timeout_seconds=0.05,
        enable_browser_fallback=False,
    ).verify(
        make_job(), run_id="run-1"
    )

    assert asyncio.get_running_loop().time() - started < 0.5
    assert receipt["status"] == "pending"
    assert "deadline" in receipt["reason"].lower()


@pytest.mark.asyncio
async def test_expired_total_deadline_skips_network_access():
    calls: list[str] = []

    async def http_loader(url: str) -> BrowserPage:
        calls.append(url)
        return make_page(url=url)

    verifier = JobLinkVerifier(
        http_loader=http_loader,
        deadline=asyncio.get_running_loop().time() - 1,
        enable_browser_fallback=False,
    )
    receipt = await verifier.verify(make_job(), run_id="run-1")

    assert receipt["status"] == "pending"
    assert calls == []


@pytest.mark.asyncio
async def test_external_cancellation_is_not_swallowed():
    entered = asyncio.Event()

    async def http_loader(url: str) -> BrowserPage:
        entered.set()
        await asyncio.sleep(10)
        return make_page(url=url)

    task = asyncio.create_task(
        JobLinkVerifier(
            http_loader=http_loader,
            enable_browser_fallback=False,
        ).verify(make_job(), run_id="run-1")
    )
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


def test_is_deliverable_requires_fresh_matching_verified_evidence():
    now = datetime.now(UTC)
    receipt = {
        "status": "verified",
        "checked_at": now.isoformat(),
        "run_id": "run-1",
        "requested_url": JOB_URL,
        "final_url": JOB_URL,
        "method": "browser",
        "reason": "Detail page matches and apply is enabled.",
        "evidence_sha256": "a" * 64,
        "title_matched": True,
        "jd_matched": True,
        "active": True,
    }
    job = make_job()
    job.link_verification = receipt

    assert is_deliverable(job, "run-1") is True
    assert is_deliverable(job, "another-run") is False

    job.link_verification = {**receipt, "method": "http"}
    assert is_deliverable(job, "run-1") is False
    job.link_verification = receipt

    for key, bad_value in (
        ("status", "pending"),
        ("requested_url", "https://jobs.example.com/positions/999"),
        ("evidence_sha256", "not-a-sha256"),
        ("title_matched", False),
        ("jd_matched", False),
        ("active", False),
    ):
        tampered = {**receipt, key: bad_value}
        job.link_verification = tampered
        assert is_deliverable(job, "run-1") is False

    job.link_verification = {key: receipt[key] for key in receipt if key != "active"}
    assert is_deliverable(job, "run-1") is False


def test_is_deliverable_rejects_stale_and_future_timestamps():
    job = make_job()
    base = {
        "status": "verified",
        "run_id": "run-1",
        "requested_url": JOB_URL,
        "final_url": JOB_URL,
        "method": "browser",
        "reason": "Detail page matches and apply is enabled.",
        "evidence_sha256": "b" * 64,
        "title_matched": True,
        "jd_matched": True,
        "active": True,
    }
    job.link_verification = {
        **base,
        "checked_at": (datetime.now(UTC) - timedelta(seconds=901)).isoformat(),
    }
    assert is_deliverable(job, "run-1") is False

    job.link_verification = {
        **base,
        "checked_at": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
    }
    assert is_deliverable(job, "run-1") is False


def test_is_deliverable_rejects_foreign_redirect_even_when_native_id_is_preserved():
    requested = "https://jobs.example.com/apply?id=abc987"
    job = make_job(requested)
    job.link_verification = {
        "status": "verified",
        "checked_at": datetime.now(UTC).isoformat(),
        "run_id": "run-1",
        "requested_url": requested,
        "final_url": "https://careers.example.org/positions/abc987",
        "method": "browser",
        "reason": "Detail page matches and apply is enabled.",
        "evidence_sha256": "c" * 64,
        "title_matched": True,
        "jd_matched": True,
        "active": True,
    }
    assert is_deliverable(job, "run-1") is False

    job.link_verification["final_url"] = "https://careers.example.org/home"
    assert is_deliverable(job, "run-1") is False


def test_is_deliverable_preserves_native_id_from_fragment_or_query():
    requested = "https://jobs.example.com/open#/position?id=abc987"
    job = make_job(requested)
    job.link_verification = {
        "status": "verified",
        "checked_at": datetime.now(UTC).isoformat(),
        "run_id": "run-1",
        "requested_url": requested,
        "final_url": "https://jobs.example.com/home?jobId=abc987",
        "method": "browser",
        "reason": "Detail page matches and apply is enabled.",
        "evidence_sha256": "d" * 64,
        "title_matched": True,
        "jd_matched": True,
        "active": True,
    }

    assert is_deliverable(job, "run-1") is True


def test_is_deliverable_recognizes_job_union_id_and_post_id_aliases():
    requested = "https://jobs.example.com/open#/position?jobUnionId=unionalpha"
    job = make_job(requested)
    job.link_verification = {
        "status": "verified",
        "checked_at": datetime.now(UTC).isoformat(),
        "run_id": "run-1",
        "requested_url": requested,
        "final_url": "https://jobs.example.com/home?postId=unionalpha",
        "method": "browser",
        "reason": "Detail page matches and apply is enabled.",
        "evidence_sha256": "e" * 64,
        "title_matched": True,
        "jd_matched": True,
        "active": True,
    }

    assert is_deliverable(job, "run-1") is True
