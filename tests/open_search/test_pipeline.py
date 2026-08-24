import pytest

from web_task_agent.open_search.models import SearchCandidate, SearchIntent
from web_task_agent.open_search.pipeline import OpenSearchPipeline


class FailingSearchProvider:
    async def search(self, query, limit=10):
        raise RuntimeError("offline")


@pytest.mark.asyncio
async def test_pipeline_returns_verified_jobs_and_trace(tmp_path):
    provider = type("Provider", (), {})()

    async def _search(query, limit=10):
        return [
            SearchCandidate(
                url="https://job-boards.greenhouse.io/example/jobs/123",
                title="Agent Intern",
                source="Example",
            )
        ]

    provider.search = _search
    result = await OpenSearchPipeline(provider).run(
        SearchIntent(raw_text="Agent"), output_dir=tmp_path, limit=1
    )
    assert result.summary.terminal_reason == "target_reached"
    assert result.jobs[0].evidence
    assert (tmp_path / "execution-trace.jsonl").exists()


@pytest.mark.asyncio
async def test_pipeline_separates_no_match_from_search_failure(tmp_path):
    result = await OpenSearchPipeline(FailingSearchProvider()).run(
        SearchIntent(raw_text="Agent"), output_dir=tmp_path
    )
    assert result.summary.terminal_reason == "search_api_error"
    assert result.failures[0].code == "search_api_error"
