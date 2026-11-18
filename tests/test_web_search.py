from time import monotonic

import pytest

from tests.fixtures.link_receipts import VerifiedLinkStub
from web_task_agent.deepseek_search import DeepSeekApiError, DeepSeekWebClient
from web_task_agent.models import BrowserPage
from web_task_agent.streamlit_runner import UiRequestError, UiRunRequest, run_ui_request, validate_ui_request


def client_with_results(calls, *, failure=None):
    def transport(endpoint, key, payload, timeout):
        calls.append(payload["query"])
        if failure:
            raise failure
        return {"results": [
            {"url": "https://example.org/jobs/1", "title": "AI Agent 实习生"},
            {"url": "https://example.org/jobs/1", "title": "duplicate"},
            {"url": "https://example.org/jobs/2", "title": "AI Agent 实习生"},
            {"url": "https://example.org/jobs/3", "title": "AI Agent 实习生"},
        ]}
    return DeepSeekWebClient(api_key="test-key", transport=transport)


@pytest.mark.parametrize("url", [
    "https://jobs.mihoyo.com/#/campus/position/9826",
    "https://campus.jd.com/#/details?id=9095",
    "https://example.com/#!/jobs/123",
])
def test_search_keeps_hash_routed_job_details(url):
    from web_task_agent.job_sources import DiscoveredJob
    from web_task_agent.web_search import _candidate_url

    assert _candidate_url(DiscoveredJob(url=url)) == url


@pytest.mark.parametrize("url", [
    "https://jobs.mihoyo.com/", "https://jobs.mihoyo.com/#/",
    "https://jobs.mihoyo.com/#/campus/position/list",
])
def test_search_still_excludes_homepages_and_hash_routed_lists(url):
    from web_task_agent.job_sources import DiscoveredJob
    from web_task_agent.web_search import _candidate_url

    assert not _candidate_url(DiscoveredJob(url=url))


@pytest.mark.asyncio
async def test_search_batches_are_deduplicated_bounded_and_keep_diagnostics():
    from web_task_agent.web_search import WebSearchBrowser

    calls = []
    browser = WebSearchBrowser(client_with_results(calls), candidate_limit=2)
    first = await browser.search("AI Agent 实习 全国", target_count=1)
    second = await browser.search("AI Agent 实习 全国", target_count=5)
    assert first[0].metadata["candidate_urls"] == ["https://example.org/jobs/1"]
    assert second[0].metadata["candidate_urls"] == ["https://example.org/jobs/2"]
    assert len(calls) == 1
    assert browser.discovered_count == 3
    assert browser.selected_count == 2
    assert not browser.search_diagnostics["has_more"]
    assert browser.source_diagnostics[0]["candidate_count"] == 3


@pytest.mark.asyncio
async def test_provider_failure_does_not_turn_into_empty_success():
    from web_task_agent.web_search import WebSearchBrowser

    browser = WebSearchBrowser(client_with_results([], failure=DeepSeekApiError(401)))
    with pytest.raises(DeepSeekApiError):
        await browser.search("AI Agent 实习", target_count=1)
    assert browser.source_diagnostics[0]["status"] == "failed"
    assert browser.source_diagnostics[0]["error_detail"] == "DeepSeek Search HTTP 401"


@pytest.mark.asyncio
async def test_expired_deadline_prevents_search_call():
    from web_task_agent.browser import PageTimeoutError
    from web_task_agent.web_search import WebSearchBrowser

    calls = []
    browser = WebSearchBrowser(client_with_results(calls), deadline=monotonic() - 1)
    with pytest.raises(PageTimeoutError):
        await browser.search("AI", target_count=1)
    assert not calls


@pytest.mark.asyncio
async def test_search_transport_timeout_is_bounded_by_shared_deadline():
    from web_task_agent.web_search import WebSearchBrowser

    timeouts = []

    def transport(endpoint, key, payload, timeout):
        timeouts.append(timeout)
        return {"results": []}

    browser = WebSearchBrowser(DeepSeekWebClient(api_key="test-key", transport=transport),
                               deadline=monotonic() + 0.1)
    await browser.search("AI Agent 实习", target_count=1)
    assert timeouts
    assert all(0 < timeout <= 0.10001 for timeout in timeouts)


def test_web_search_requires_its_key_even_with_models_disabled():
    request = UiRunRequest(data_mode="web_search", engine="hybrid")
    with pytest.raises(UiRequestError, match="DEEPSEEK_API_KEY"):
        validate_ui_request(request, environ={})
    validate_ui_request(request, environ={"DEEPSEEK_API_KEY": "test-key"})


@pytest.mark.asyncio
async def test_ui_web_search_runs_search_tool_archives_candidates_and_delivers(tmp_path, monkeypatch):
    import web_task_agent.web_search as search

    calls = []
    monkeypatch.setattr(search, "build_search_client", lambda: client_with_results(calls))

    async def load(url):
        if not url.endswith("3"):
            return BrowserPage(url=url, title="Unavailable candidate", content="Not a job description. " * 12)
        return BrowserPage(url=url, title="AI Agent 实习生", content=(
            "公司：Example\n工作地点：北京\n岗位职责：开发 AI Agent 和 RAG 工作流。\n"
            "任职要求：熟悉 Python，岗位为实习，至少实习3个月。\n" * 4))

    result = await run_ui_request(
        UiRunRequest(data_mode="web_search", engine="hybrid", target_count=1),
        output_root=tmp_path, environ={"DEEPSEEK_API_KEY": "test-key"},
        page_loader=load, link_verifier=VerifiedLinkStub(),
    )
    assert calls
    assert result.agent_trace[0]["action"] == "search_jobs"
    assert result.agent_trace[0]["observation"]["success"]
    assert result.run_status == "succeeded"
    assert result.saved and len(result.jobs) == 1
    assert result.progress["discovered"] == 3
    assert result.progress["selected"] == 3
    assert result.progress["checked"] == 3
    assert result.source_mode == "web_search"
    assert result.source_metrics["snapshot_integrity"]["value"] == 1


def test_web_search_is_a_billable_run_and_appears_in_ui():
    from web_task_agent.streamlit_app import MODE_LABELS, mode_hint, should_consume_llm_budget

    assert MODE_LABELS["网页搜索"] == "web_search"
    assert "DeepSeek Search" in mode_hint("web_search")
    assert should_consume_llm_budget(UiRunRequest(data_mode="web_search"))


def test_curated_sources_exclude_unvalidated_or_internship_incompatible_sources():
    from web_task_agent.source_catalog import VERIFIED_SOURCES

    assert {"xiaomi", "xiaohongshu", "xpeng", "netease", "mihoyo", "liauto", "nio"} <= set(VERIFIED_SOURCES)
    assert "agibot" not in VERIFIED_SOURCES
    assert all(row["checked_on"] == "2026-11-18" for row in VERIFIED_SOURCES.values())


@pytest.mark.asyncio
async def test_web_search_capture_preserves_actual_detail_resolution_attempts(tmp_path):
    from web_task_agent.source_capture import SourceCaptureBrowser
    from web_task_agent.web_search import WebSearchBrowser

    async def load(url):
        return BrowserPage(url=url, title="AI Agent 实习生", content="真实公开岗位正文与申请说明。" * 20)

    browser = WebSearchBrowser(client_with_results([]), http_loader=load)
    capture = SourceCaptureBrowser(browser, root=tmp_path / "archive", run_id="run-search-capture",
                                   keyword="AI Agent 实习", location="全国", target_count=1, mode="web_search")
    pages = await capture.search("AI Agent 实习", target_count=1)
    await capture.open_url(pages[0].metadata["candidate_urls"][0])
    assert capture.resolution_log == browser.resolution_log
    assert capture.resolution_log
    assert "http" in [attempt.strategy for attempt in capture.archive.read_attempts()]


@pytest.mark.asyncio
async def test_failed_search_is_retained_in_the_public_archive(tmp_path):
    from web_task_agent.source_capture import SourceCaptureBrowser
    from web_task_agent.web_search import WebSearchBrowser

    browser = WebSearchBrowser(client_with_results([], failure=DeepSeekApiError(500)))
    capture = SourceCaptureBrowser(browser, root=tmp_path / "archive", run_id="run-search-failure",
                                   keyword="AI Agent 实习", location="全国", target_count=1, mode="web_search")
    with pytest.raises(DeepSeekApiError):
        await capture.search("AI Agent 实习", target_count=1)
    attempts = capture.archive.read_attempts()
    assert len(attempts) == 1
    assert attempts[0].operation_kind == "search"
    assert attempts[0].status == "error"


@pytest.mark.asyncio
async def test_search_hitl_approval_preserves_progress_and_recomputes_delivered_evidence(tmp_path, monkeypatch):
    import web_task_agent.web_search as search
    from web_task_agent.agent_approval import ApprovalDecision
    from web_task_agent.streamlit_runner import resume_ui_request

    calls = []
    monkeypatch.setattr(search, "build_search_client", lambda: client_with_results(calls))

    async def load(url):
        return BrowserPage(url=url, title="AI Agent 实习生", source="http", content=(
            "公司：Example\n工作地点：北京\n岗位职责：开发 AI Agent 和 RAG。\n任职要求：Python，岗位为实习。\n" * 4))

    pending = await run_ui_request(
        UiRunRequest(data_mode="web_search", engine="hybrid", hitl=True, target_count=1),
        output_root=tmp_path, owner_id="test-owner", environ={"DEEPSEEK_API_KEY": "test-key"},
        page_loader=load, link_verifier=VerifiedLinkStub())
    assert pending.run_status == "awaiting_approval"
    resumed = await resume_ui_request(
        pending.run_id, ApprovalDecision(approval_id=pending.approval["approval_id"], outcome="approve"),
        owner_id="test-owner", output_root=tmp_path, environ={})
    assert resumed.run_status == "succeeded"
    assert resumed.progress["discovered"] == pending.progress["discovered"] == 3
    assert resumed.progress["selected"] == pending.progress["selected"] == 3
    assert resumed.source_evidence
    assert resumed.source_metrics["deliveries_by_origin"]
    assert len(calls) == 1
