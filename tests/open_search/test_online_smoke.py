import json

import pytest

from web_task_agent.open_search.models import SearchCandidate
from web_task_agent.open_search.online_smoke import DEFAULT_QUERIES, main, run_online_smoke
from web_task_agent.open_search.source_verifier import SourceVerdict


class RecordingProvider:
    last_malformed_count = 1

    def __init__(self):
        self.queries = []

    async def search(self, query, limit=10):
        self.queries.append((query, limit))
        return [
            SearchCandidate(
                url="https://job-boards.greenhouse.io/example/jobs/123",
                title="Agent Intern",
                snippet="Python LangGraph internship",
                source="Example AI",
            )
        ]


class FailingProvider:
    async def search(self, query, limit=10):
        raise RuntimeError("provider unavailable")


class ReachableVerifier:
    async def verify_reachable(self, url):
        return SourceVerdict(
            True,
            url,
            "greenhouse",
            "reachable",
            content_hash="a" * 64,
        )


@pytest.mark.asyncio
async def test_online_smoke_writes_json_markdown_and_per_query_artifacts(tmp_path):
    provider = RecordingProvider()

    report = await run_online_smoke(
        ["北京 Agent 实习，1 个岗位", "remote AI internship, top 1 jobs"],
        output_dir=tmp_path,
        provider=provider,
        source_verifier=ReachableVerifier(),
    )

    assert report["mode"] == "online"
    assert report["provider"] == "RecordingProvider"
    assert report["query_count"] == 2
    assert report["total_verified_jobs"] == 2
    assert report["failure_counts"] == {}
    assert report["runs"][0]["provider_quality"]["malformed_candidates"] == 1
    assert provider.queries == [
        ("北京 Agent 实习，1 个岗位", 2),
        ("remote AI internship, top 1 jobs", 2),
    ]
    assert (tmp_path / "runs" / "query-01" / "jobs.jsonl").exists()
    assert (tmp_path / "runs" / "query-02" / "run-summary.json").exists()

    saved = json.loads((tmp_path / "online-smoke-report.json").read_text(encoding="utf-8"))
    markdown = (tmp_path / "online-smoke-report.md").read_text(encoding="utf-8")
    assert saved == report
    assert "真实在线验收报告" in markdown
    assert "RecordingProvider" in markdown
    assert "北京 Agent 实习，1 个岗位" in markdown


@pytest.mark.asyncio
async def test_online_smoke_aggregates_search_failure_without_crashing(tmp_path):
    report = await run_online_smoke(
        ["Agent internship"],
        output_dir=tmp_path,
        provider=FailingProvider(),
        source_verifier=ReachableVerifier(),
    )

    assert report["total_verified_jobs"] == 0
    assert report["failure_counts"] == {"search_api_error": 1}
    assert report["runs"][0]["terminal_reason"] == "search_api_error"


def test_online_smoke_defaults_to_three_queries():
    assert len(DEFAULT_QUERIES) == 3
    assert any("北京" in query for query in DEFAULT_QUERIES)
    assert any("remote" in query.casefold() for query in DEFAULT_QUERIES)


def test_online_smoke_cli_fails_cleanly_without_tavily_key(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    exit_code = main(["--output-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "TAVILY_API_KEY" in captured.err
    assert not (tmp_path / "online-smoke-report.json").exists()
