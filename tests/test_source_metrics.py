import pytest

from web_task_agent.browser import BrowserUseClient
from web_task_agent.job_sources import AggregatorPageLoader, DiscoveredJob
from web_task_agent.models import BrowserPage
from web_task_agent.source_capture import SourceCaptureBrowser


@pytest.mark.asyncio
async def test_source_metrics_keep_detail_list_empty_and_error_denominators_separate(tmp_path):
    from web_task_agent.source_metrics import build_source_metrics
    body = "Title: AI intern\nCompany: Example\nLocation: Remote\nRequirements: Python AI\n" * 3
    jobs = [DiscoveredJob(url=f"https://example.com/jobs/{i}", source="fixture", source_job_id=str(i), jd_text=body)
            for i in (1, 2)]

    async def http(url):
        if url.endswith("2"):
            raise TimeoutError("must not appear in public metrics")
        return BrowserPage(url=url, content=body, source="http")

    capture = SourceCaptureBrowser(BrowserUseClient(page_loader=AggregatorPageLoader(jobs, http_loader=http)),
                                   root=tmp_path / "archive", run_id="run-metrics", keyword="AI intern",
                                   location="Remote", target_count=2, jobs=jobs)
    pages = [await capture.open_url(job.url) for job in jobs]
    capture.finalize()
    summary, evidence = build_source_metrics(
        capture.archive, source_diagnostics=[{"source": "one", "status": "healthy", "count": 2},
                                            {"source": "two", "status": "empty", "count": 0},
                                            {"source": "three", "status": "failed", "count": 0}],
        delivered_urls=[page.url for page in pages], verified_urls=[pages[0].url],
        verification_urls=[page.url for page in pages],
    )
    assert summary["source_callable"] == {"numerator": 2, "denominator": 3, "value": 2 / 3}
    assert summary["sources_with_candidates"]["numerator"] == 1
    assert summary["source_empty"] == 1
    assert summary["source_error"] == 1
    assert summary["detail_body_fetch"] == {"numerator": 1, "denominator": 2, "value": 0.5}
    assert summary["deliveries_by_origin"] == {"http": 1, "list": 1}
    assert summary["conditions_passed"]["value"] == 0.5
    assert summary["snapshot_integrity"]["value"] == 1
    assert evidence[pages[1].url]["evidence_level"] == "list"
    assert evidence[pages[1].url]["availability"] == "unknown"
    assert evidence[pages[1].url]["observed_at"]
    assert "must not appear" not in str(summary)


def test_empty_evidence_never_claims_perfect_source_rate(tmp_path):
    from web_task_agent.source_archive import SourceArchive
    from web_task_agent.source_metrics import build_source_metrics
    summary, evidence = build_source_metrics(SourceArchive(tmp_path / "missing"))
    assert summary["source_callable"]["value"] is None
    assert summary["detail_body_fetch"]["value"] is None
    assert summary["snapshot_integrity"]["value"] is None
    assert summary["conditions_passed"]["value"] is None
    assert evidence == {}
