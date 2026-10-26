import json

import pytest

from web_task_agent.browser import PageEmptyError
from web_task_agent.deepseek_search import DeepSeekWebClient
from web_task_agent.job_sources import DiscoveredJob
from web_task_agent.models import BrowserPage


@pytest.fixture(autouse=True)
def offline_orchestration_link_verifier(monkeypatch):
    from tests.fixtures.link_receipts import VerifiedLinkStub

    monkeypatch.setattr("web_task_agent.link_verification.JobLinkVerifier", lambda **kwargs: VerifiedLinkStub())


def body(company="Existing"):
    return (
        f"Title: AI Agent 实习生\nCompany: {company}\nLocation: 上海\n"
        "Requirements: 熟悉 Python 和大模型、AI Agent 应用开发，掌握 RAG、工具调用和测试。\n"
        "Responsibilities: 开发 AI Agent 工作流，分析评测结果，改进工具调用可靠性和岗位搜索质量。\n"
    ) * 2


def test_candidate_merge_has_equal_cap_and_keeps_official_metadata():
    from web_task_agent.search_comparison import merge_candidates

    official = [DiscoveredJob(url=f"https://example.org/jobs/{i}", company="Existing") for i in range(4)]
    search = [DiscoveredJob(url=official[0].url, source="deepseek-search"),
              DiscoveredJob(url="https://example.org/jobs/new", source="deepseek-search")]
    merged = merge_candidates(official, search, limit=3)
    assert len(merged) == 3
    assert len({job.url for job in merged}) == 3
    assert any(job.url.endswith("/new") for job in merged)
    assert merged[0].company == "Existing"


@pytest.mark.asyncio
async def test_two_arms_share_official_inputs_and_report_uncapped_valid_gain(tmp_path, monkeypatch):
    from tests.fixtures.link_receipts import VerifiedLinkStub
    from web_task_agent.search_comparison import ComparisonCase, run_search_comparison

    monkeypatch.setattr("web_task_agent.link_verification.JobLinkVerifier", lambda **kwargs: VerifiedLinkStub())

    discoveries = []
    calls = []

    class Source:
        diagnostics = []

        async def discover(self, limit):
            discoveries.append(limit)
            return [DiscoveredJob(url="https://example.org/jobs/old", company="Existing")]

    async def load(url):
        if url.endswith("old"):
            return BrowserPage(url=url, content=body())
        raise PageEmptyError("empty")

    def transport(endpoint, key, payload, timeout):
        calls.append(endpoint)
        if endpoint == "search":
            return {"results": [{"url": "https://example.org/jobs/new", "title": "AI Agent 实习生",
                                 "summary": "Not trusted as a JD"}]}
        return {"title": "AI Agent 实习生", "content": body("NewCompany")}

    result = await run_search_comparison(
        cases=[ComparisonCase(keyword="AI Agent 实习", location="上海", target_count=1)],
        output_dir=tmp_path / "comparison", candidate_budget=4,
        source_factory=lambda case: Source(), page_loader=load,
        client_factory=lambda: DeepSeekWebClient(api_key="test-secret", transport=transport),
    )
    assert discoveries == [4]
    paired = result["cases"][0]
    assert list(paired["arms"]) == ["baseline", "search_fetch"]
    baseline, augmented = paired["arms"].values()
    assert baseline["api_usage"]["search"]["attempts"] == 0
    assert baseline["valid_count"] == 1
    assert augmented["valid_count"] == 2
    assert len(augmented["recommendations"]) == 1
    assert augmented["candidate_count"] <= 4
    assert paired["delta"]["valid_count"] == 1
    assert paired["delta"]["new_company_count"] == 1
    assert augmented["fetch_diagnostics"][0]["status"] == "recovered"
    assert "fetch" in calls
    saved = (tmp_path / "comparison" / "comparison.json").read_text(encoding="utf-8")
    assert "test-secret" not in saved
    assert json.loads(saved)["cases"][0]["delta"] == paired["delta"]
    assert (tmp_path / "comparison" / "comparison.md").is_file()


@pytest.mark.asyncio
async def test_search_failure_keeps_baseline_candidates_and_safe_errors(tmp_path):
    from web_task_agent.search_comparison import ComparisonCase, run_search_comparison

    class Source:
        diagnostics = []

        async def discover(self, limit):
            return [DiscoveredJob(url="https://example.org/jobs/old")]

    async def load(url):
        return BrowserPage(url=url, content=body())

    def failed(*args):
        from web_task_agent.deepseek_search import DeepSeekApiError
        raise DeepSeekApiError(401)

    result = await run_search_comparison(
        cases=[ComparisonCase(keyword="AI Agent 实习", location="上海")], output_dir=tmp_path / "failed",
        candidate_budget=2, source_factory=lambda case: Source(), page_loader=load,
        client_factory=lambda: DeepSeekWebClient(api_key="secret", transport=failed),
    )
    arms = result["cases"][0]["arms"]
    assert arms["baseline"]["valid_count"] == arms["search_fetch"]["valid_count"] == 1
    assert arms["search_fetch"]["search_diagnostics"][0]["api_status"] == 401
    assert arms["search_fetch"]["api_usage"]["search"]["attempts"] == 1


@pytest.mark.asyncio
async def test_no_candidates_never_triggers_implicit_browser_search(tmp_path):
    from web_task_agent.search_comparison import ComparisonCase, run_search_comparison

    class Source:
        diagnostics = []

        async def discover(self, limit):
            return []

    async def forbidden(url):
        pytest.fail("No browser calls without candidates")

    result = await run_search_comparison(
        cases=[ComparisonCase(keyword="AI 实习", location="上海")], output_dir=tmp_path / "empty",
        source_factory=lambda case: Source(), page_loader=forbidden,
        client_factory=lambda: DeepSeekWebClient(api_key="secret", transport=lambda *args: {"results": []}),
    )
    assert all(arm["candidate_count"] == arm["valid_count"] == 0
               for arm in result["cases"][0]["arms"].values())
