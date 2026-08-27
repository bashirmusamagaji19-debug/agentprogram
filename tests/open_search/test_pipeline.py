import pytest

from web_task_agent.open_search.models import SearchCandidate, SearchIntent
from web_task_agent.open_search.pipeline import OpenSearchPipeline
from web_task_agent.open_search.source_verifier import SourceVerdict

DETAIL_HTML = """
<html><head><script type="application/ld+json">
{
  "@type": "JobPosting",
  "title": "Detail Page Agent Intern",
  "hiringOrganization": {"name": "Detail Page AI"},
  "jobLocation": {"address": {"addressLocality": "Beijing", "addressCountry": "CN"}},
  "employmentType": "INTERN",
  "description": "Build trustworthy agents",
  "qualifications": "Python and LangGraph"
}
</script></head><body>Detail Page Agent Intern</body></html>
""".strip()


class FailingSearchProvider:
    async def search(self, query, limit=10):
        raise RuntimeError("offline")


class ReachableVerifier:
    def verify_url(self, url):
        return SourceVerdict(True, url, "fixture", "trusted")

    async def verify_reachable(self, url):
        return SourceVerdict(
            True,
            url,
            "fixture",
            "reachable",
            content_hash="a" * 64,
            page_html=DETAIL_HTML,
        )


class ProviderWithMalformedCount:
    last_malformed_count = 2

    async def search(self, query, limit=10):
        return [
            SearchCandidate(
                url="https://job-boards.greenhouse.io/example/jobs/123",
                title="Search Summary Title",
                source="Example",
                metadata={"page_html": DETAIL_HTML},
            )
        ]


@pytest.mark.asyncio
async def test_pipeline_returns_verified_jobs_and_trace(tmp_path):
    provider = type("Provider", (), {})()

    async def _search(query, limit=10):
        return [
            SearchCandidate(
                url="https://job-boards.greenhouse.io/example/jobs/123",
                title="Search Summary Clickbait",
                source="Example",
                metadata={"page_html": DETAIL_HTML},
            )
        ]

    provider.search = _search
    result = await OpenSearchPipeline(provider).run(
        SearchIntent(raw_text="Agent"), output_dir=tmp_path, limit=1
    )
    assert result.summary.terminal_reason == "target_reached"
    assert result.jobs[0].title == "Detail Page Agent Intern"
    assert result.jobs[0].company == "Detail Page AI"
    assert result.jobs[0].evidence
    assert (tmp_path / "execution-trace.jsonl").exists()


@pytest.mark.asyncio
async def test_pipeline_preserves_caller_run_id_in_summary(tmp_path):
    provider = type("Provider", (), {})()

    async def _search(query, limit=10):
        return []

    provider.search = _search
    result = await OpenSearchPipeline(provider).run(
        SearchIntent(raw_text="Agent"),
        output_dir=tmp_path,
        limit=1,
        run_id="api-run-123",
    )

    assert result.summary.run_id == "api-run-123"
    assert result.summary.finished_at is not None


@pytest.mark.asyncio
async def test_pipeline_separates_no_match_from_search_failure(tmp_path):
    result = await OpenSearchPipeline(FailingSearchProvider()).run(
        SearchIntent(raw_text="Agent"), output_dir=tmp_path
    )
    assert result.summary.terminal_reason == "search_api_error"
    assert result.failures[0].code == "search_api_error"


@pytest.mark.asyncio
async def test_pipeline_records_reachability_hash_in_trace(tmp_path):
    provider = type("Provider", (), {})()

    async def _search(query, limit=10):
        return [
            SearchCandidate(
                url="https://job-boards.greenhouse.io/example/jobs/123",
                title="Search Summary Clickbait",
                source="Example",
            )
        ]

    provider.search = _search
    result = await OpenSearchPipeline(
        provider,
        source_verifier=ReachableVerifier(),
        verify_reachability=True,
    ).run(SearchIntent(raw_text="Agent"), output_dir=tmp_path, limit=1)
    assert result.jobs
    assert result.jobs[0].title == "Detail Page Agent Intern"
    assert all(evidence.content_hash == "a" * 64 for evidence in result.jobs[0].evidence)
    trace = (tmp_path / "execution-trace.jsonl").read_text(encoding="utf-8")
    assert "reachability_checked" in trace
    assert "a" * 64 in trace


@pytest.mark.asyncio
async def test_pipeline_summary_records_provider_quality_metadata(tmp_path):
    result = await OpenSearchPipeline(ProviderWithMalformedCount()).run(
        SearchIntent(raw_text="Agent"), output_dir=tmp_path, limit=1
    )
    assert result.summary.metadata["malformed_candidates"] == 2
    assert result.summary.metadata["reachability_checked"] is False
    assert result.summary.metadata["final_fields_source"] == "detail_page"
    assert result.summary.metadata["extraction_methods"] == {"json_ld": 1}
    assert result.summary.metadata["evidence_complete_jobs"] == 1


@pytest.mark.asyncio
async def test_pipeline_rejects_trusted_candidate_without_detail_content(tmp_path):
    provider = type("Provider", (), {})()

    async def _search(query, limit=10):
        return [
            SearchCandidate(
                url="https://job-boards.greenhouse.io/example/jobs/123",
                title="Search Summary Must Not Become A Job",
                source="Example",
            )
        ]

    provider.search = _search
    result = await OpenSearchPipeline(provider).run(
        SearchIntent(raw_text="Agent"), output_dir=tmp_path, limit=1
    )

    assert result.jobs == []
    assert result.failures[0].code == "extraction_incomplete"
    assert result.summary.terminal_reason == "budget_exhausted"
