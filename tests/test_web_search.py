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
    "https://talent.baidu.com/jobs/detail/INTERN/af00a921-47c2-42af-b2b4-389810123454",
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
    browser = WebSearchBrowser(client_with_results(calls), candidate_limit=2, extra_hosts=["example.org"])
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
async def test_web_search_prefilters_by_employer_and_single_job_identity():
    from web_task_agent.web_search import WebSearchBrowser

    official = "https://hr.163.com/job-detail.html?id=65260"
    custom = "https://careers.example.org/jobs/21"
    def transport(endpoint, key, payload, timeout):
        return {"results": [
            {"url": "https://unknown.jobs.feishu.cn/campus/position/123/detail", "title": "AI Agent 开发实习生"},
            {"url": "https://hr.163.com.evil.example/jobs/9", "title": "网易 AI Agent 实习生"},
            {"url": "https://hr.163.com/campus-recruitment.html", "title": "AI 研发招聘公告"},
            {"url": official + "&from=search#1", "title": "AI Agent 开发实习生"},
            {"url": official, "title": "AI Agent 开发实习生"},
            {"url": custom, "title": "LLM 开发实习生"},
            {"url": "https://x.careers.example.org/jobs/21", "title": "LLM 开发实习生"},
        ]}
    browser = WebSearchBrowser(DeepSeekWebClient(api_key="test-key", transport=transport),
                               extra_hosts=["careers.example.org"])
    page = (await browser.search("AI Agent 实习 深圳", target_count=3))[0]
    assert set(page.metadata["candidate_urls"]) == {official, custom}
    diagnostics = page.metadata["search_diagnostics"]
    assert diagnostics["filtered_outside_employers"] >= 3
    assert diagnostics["filtered_not_detail"] >= 1
    assert diagnostics["filtered_duplicate"] >= 1
    assert all("site:" in query and "深圳" in query for query in diagnostics["queries"])


@pytest.mark.asyncio
async def test_search_checks_different_employers_before_repeated_hits():
    from web_task_agent.web_search import WebSearchBrowser

    urls = [f"https://example.org/jobs/{i}" for i in range(1, 6)]
    other = "https://example.com/jobs/101"
    client = DeepSeekWebClient(api_key="test-key", transport=lambda *args: {"results": [
        {"url": url, "title": "AI Agent internship"} for url in [*urls, other]
    ]})
    browser = WebSearchBrowser(client, extra_hosts=["example.org", "example.com"])

    pages = await browser.search("AI Agent", target_count=2)
    assert pages[0].metadata["candidate_urls"] == [urls[0], other]


@pytest.mark.asyncio
async def test_web_search_browser_fallback_rejects_unrendered_job_shell():
    from web_task_agent.browser import PageEmptyError
    from web_task_agent.web_search import WebSearchBrowser

    class ShellBrowser:
        async def open_url(self, url):
            return BrowserPage(url=url, title="Talent portal", content="Home | Job search | Sign in " * 30)

    browser = WebSearchBrowser(client_with_results([]), extra_hosts=["example.org"], browser_loader=ShellBrowser())
    pages = await browser.search("AI Agent 实习", target_count=1)
    with pytest.raises(PageEmptyError):
        await browser.open_url(pages[0].metadata["candidate_urls"][0])


@pytest.mark.asyncio
async def test_web_search_supplements_second_batch_with_attributed_official_job(tmp_path):
    from web_task_agent.job_sources import DiscoveredJob
    from web_task_agent.source_capture import SourceCaptureBrowser
    from web_task_agent.web_search import WebSearchBrowser

    public_url = "https://example.org/jobs/10"
    native = DiscoveredJob(url=public_url, title="AI Agent developer", company="Example",
                           source="example-employer", source_job_id="10",
                           discovery_url="https://example.org/jobs")
    official_calls = []

    async def official_discovery(target_count):
        official_calls.append(target_count)
        return [native], [{"source": "example-employer", "status": "healthy", "count": 1}]

    browser = WebSearchBrowser(client_with_results([]), extra_hosts=["example.org"],
                               official_discovery=official_discovery)
    capture = SourceCaptureBrowser(browser, root=tmp_path / "archive", run_id="run-web-supplement",
                                   keyword="AI Agent", location="全国", target_count=1)
    first = await capture.search("AI Agent", target_count=1)
    assert first[0].metadata["candidate_urls"] == ["https://example.org/jobs/1"]
    assert official_calls == []
    second = await capture.search("AI Agent", target_count=1)

    assert second[0].metadata["candidate_urls"] == [public_url]
    assert official_calls == [1]
    assert any(r.source_spec == native.source and r.source_job_id == "10" for r in capture.candidates.values())
    assert capture.source_diagnostics[-1]["source"] == "example-employer"


@pytest.mark.parametrize(("duties", "valid"), [
    ("开发 Agent 工作流并实现工具调用。负责上线后的系统评测。", True),
    ("整理海外数据。负责用户运营。日常使用 AI 工具提升效率。", False),
    ("整理海外运营数据。负责内容运营。协助开发 Agent 工作流。", False),
    ("Develop and deploy agent workflows. Maintain internal usage documentation.", True),
])
def test_development_role_requires_substantive_duties(duties, valid):
    from web_task_agent.models import JobPosting
    from web_task_agent.verifier import JobVerifier

    job = JobPosting(title="AI Agent 开发实习生", company="示例", location="深圳", source="http",
                     url="https://hr.163.com/job-detail.html?id=65260", responsibilities=duties,
                     requirements="熟悉 Python 和 Agent 项目开发", confidence=1)
    assert JobVerifier(require_development=True).verify(job).is_valid is valid


@pytest.mark.asyncio
async def test_unarchivable_hit_does_not_abort_or_leak_in_a_search_batch(tmp_path):
    from web_task_agent.source_capture import SourceCaptureBrowser
    from web_task_agent.web_search import WebSearchBrowser

    valid = "https://example.org/#/jobs/123"
    urls = ["https://[bad", "https://example.org/jobs/1?token=example-secret",
            "https://example.org/#/jobs/2?token=example-secret", valid,
            "https://example.org/#/jobs/124"]
    client = DeepSeekWebClient(api_key="test-key", transport=lambda *args: {"results": [
        {"url": url, "title": "AI Agent internship"} for url in urls
    ]})
    browser = SourceCaptureBrowser(WebSearchBrowser(client, extra_hosts=["example.org"]),
                                   root=tmp_path / "archive", run_id="run-search", keyword="AI Agent",
                                   location="全国", target_count=2)

    pages = await browser.search("AI Agent", target_count=2)
    assert pages[0].metadata["candidate_urls"] == [valid, urls[-1]]
    assert pages[0].metadata["search_diagnostics"]["filtered_invalid_url"] == 2
    assert {record.requested_url for record in browser.candidates.values()} >= {valid, urls[-1]}
    assert all("example-secret" not in path.read_text(encoding="utf-8", errors="ignore")
               for path in (tmp_path / "archive").rglob("*.json"))


@pytest.mark.parametrize(("title", "valid"), [
    ("AI产品（J104948）", False),
    ("AI 产品经理实习生", False),
    ("AI 产品开发工程师", True),
])
def test_development_role_does_not_confuse_product_roles_with_engineering(title, valid):
    from web_task_agent.models import JobPosting
    from web_task_agent.verifier import JobVerifier

    job = JobPosting(title=title, company="示例", location="深圳", source="http",
                     url="https://example.org/jobs/1", responsibilities="开发 AI Agent 工作流。", confidence=1)
    assert JobVerifier(require_development=True).verify(job).is_valid is valid


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
        UiRunRequest(data_mode="web_search", engine="hybrid", target_count=1,
                     web_search_hosts=["example.org"]),
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

    assert {"xiaomi", "xiaohongshu", "xpeng", "meituan", "netease", "mihoyo", "nio",
            "huawei", "baidu"} <= VERIFIED_SOURCES.keys()
    assert "liauto" not in VERIFIED_SOURCES
    assert "agibot" not in VERIFIED_SOURCES
    assert all(row["checked_on"] == "2026-11-18" for row in VERIFIED_SOURCES.values())


@pytest.mark.asyncio
async def test_web_search_capture_preserves_actual_detail_resolution_attempts(tmp_path):
    from web_task_agent.source_capture import SourceCaptureBrowser
    from web_task_agent.web_search import WebSearchBrowser

    async def load(url):
        return BrowserPage(url=url, title="AI Agent 实习生", content="真实公开岗位正文与申请说明。" * 20)

    browser = WebSearchBrowser(client_with_results([]), http_loader=load, extra_hosts=["example.org"])
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
        UiRunRequest(data_mode="web_search", engine="hybrid", hitl=True, target_count=1,
                     web_search_hosts=["example.org"]),
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
