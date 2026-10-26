from datetime import UTC, datetime, timedelta

import pytest

from web_task_agent.browser import BrowserUseClient, FakeBrowserClient
from web_task_agent.job_sources import AggregatorPageLoader, DiscoveredJob
from web_task_agent.models import BrowserPage, UserProfile
from web_task_agent.source_archive import SourceArchive
from web_task_agent.storage import JobRepository

from .test_agent_multitarget import make_workflow


@pytest.mark.asyncio
async def test_capture_preserves_each_layer_and_exact_context(tmp_path):
    from web_task_agent.source_capture import SourceCaptureBrowser
    url = "https://example.com/jobs/1"
    body = "Title: AI intern\nCompany: Example\nLocation: Remote\nRequirements: Python AI\n" * 3
    job = DiscoveredJob(url=url, title="AI intern", company="Example", location="Remote",
                        source="fixture", source_job_id="1", jd_text=body)

    class BrokenApi:
        async def fetch(self, url):
            raise TimeoutError("private raw transport error")

    async def empty_http(url):
        return BrowserPage(url=url, content="")

    loader = AggregatorPageLoader([job], official_api=BrokenApi(), http_loader=empty_http)
    capture = SourceCaptureBrowser(BrowserUseClient(page_loader=loader, discovery_only=True),
                                   root=tmp_path / "archive", run_id="run-layers", jobs=[job],
                                   keyword="AI intern", location="Remote", target_count=1)
    page = await capture.open_url(url)
    manifest = capture.finalize()
    archive = SourceArchive(capture.archive.root)
    attempts = archive.read_attempts()
    assert len(attempts) == 3
    assert [a.sequence for a in attempts] == [1, 2, 3]
    assert [a.status for a in attempts] == ["error", "error", "success"]
    assert all(a.started_at.tzinfo and a.elapsed_ms >= 0 for a in attempts)
    assert attempts[-1].snapshot_id == page.metadata["snapshot_id"]
    restored = archive.read_page(page.metadata["snapshot_id"])
    assert restored.content == body
    assert restored.metadata["discovered_company"] == "Example"
    assert restored.metadata["evidence_level"] == "list"
    assert archive.read_manifest().run_id == "run-layers"
    assert manifest.is_file()
    assert archive.verify()
    assert "private raw" not in archive.attempts_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_full_shared_service_replay_cannot_access_network_or_model(tmp_path, monkeypatch):
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunOptions, start_agent_run
    from web_task_agent.source_capture import SourceCaptureBrowser
    from web_task_agent.source_replay import ArchiveBrowser
    workflow, pages = make_workflow(tmp_path / "live")
    capture = SourceCaptureBrowser(FakeBrowserClient(pages), root=tmp_path / "archive",
                                   run_id="run-source", keyword="AI intern", location="Remote",
                                   target_count=3, seed_urls=[p.url for p in pages])
    workflow.browser = capture
    user = UserProfile(keyword="AI intern", location="Remote", target_count=3, seed_urls=[p.url for p in pages])
    live = await start_agent_run(user, run_id="run-source", owner_id="owner",
                                services=AgentServices(workflow=workflow, run_dir=tmp_path / "live"),
                                options=AgentRunOptions(engine="hybrid"))
    capture.finalize()

    def fail_network(*args, **kwargs):
        raise AssertionError("network/model must not be used in replay")

    import socket
    import urllib.request

    import web_task_agent.agent_planner as planner
    monkeypatch.setattr(socket.socket, "connect", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    monkeypatch.setattr(planner, "build_configured_agent_planner", fail_network)
    replay_workflow, _ = make_workflow(tmp_path / "replay")
    replay_workflow.browser = ArchiveBrowser(tmp_path / "archive")
    replay = await start_agent_run(user, run_id="run-replay", owner_id="owner",
                                  services=AgentServices(workflow=replay_workflow, run_dir=tmp_path / "replay"),
                                  options=AgentRunOptions(engine="hybrid"))
    assert live.status == replay.status == "succeeded"
    assert [j.model_dump() for j in live.view.jobs] == [j.model_dump() for j in replay.view.jobs]
    assert [d.action for d in live.agent_state.decision_history] == [
        d.action for d in replay.agent_state.decision_history
    ]
    assert replay.agent_state.metrics.planner_calls == 0


def test_cache_preserves_public_context_and_original_observation(tmp_path):
    repo = JobRepository(tmp_path / "cache.sqlite")
    repo.initialize()
    observed = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    page = BrowserPage(url="https://example.com/1", content="public job body", metadata={
        "candidate_id": "candidate-1", "snapshot_id": "snap-1", "observed_at": observed,
        "discovered_company": "Example", "discovered_location": "Remote", "content_origin": "http",
        "Authorization": "must-not-be-cached", "resume_text": "private candidate resume",
    })
    repo.cache_page(page)
    restored = repo.get_cached_page(page.url)
    assert restored.metadata["observed_at"] == observed
    assert restored.metadata["candidate_id"] == "candidate-1"
    assert restored.metadata["snapshot_id"] == "snap-1"
    assert restored.metadata["discovered_company"] == "Example"
    assert restored.metadata["cache_hit"] is True
    assert restored.metadata["cache_age_seconds"] >= 3600
    assert "Authorization" not in restored.metadata
    assert "resume_text" not in restored.metadata


@pytest.mark.asyncio
async def test_ui_uses_public_builder_and_exports_source_manifest(tmp_path, monkeypatch):
    import web_task_agent.agent_components as components
    import web_task_agent.streamlit_runner as runner
    from web_task_agent.source_archive import SourceArchive
    calls = []
    real_builder = components.build_browser_components

    async def builder(*args, **kwargs):
        calls.append(True)
        return await real_builder(*args, **kwargs)

    monkeypatch.setattr(components, "build_browser_components", builder)
    result = await runner.run_ui_request(
        runner.UiRunRequest(keyword="AI intern", location="全国", target_count=2, engine="hybrid"),
        output_root=tmp_path, environ={},
    )
    assert calls == [True]
    assert result.source_manifest
    manifest_path = result.artifacts["source_manifest"]
    assert manifest_path.is_file()
    archive = SourceArchive(manifest_path.parent)
    assert archive.verify()
    search_ids = {c.candidate_id for c in archive.read_candidates() if c.query_context.get("type") == "search_page"}
    assert len(search_ids) == 2
    job_snapshots = [sid for sid in archive.read_manifest().snapshot_ids
                     if archive.read_page(sid).metadata["candidate_id"] not in search_ids]
    assert len(job_snapshots) == 2
    assert result.source_mode == "demo"
    assert all(job.url for job in result.jobs)


def test_distinct_native_candidates_at_same_url_are_not_discarded(tmp_path):
    from web_task_agent.source_capture import SourceCaptureBrowser
    url = "https://example.com/jobs/notice"
    capture = SourceCaptureBrowser(FakeBrowserClient([]), root=tmp_path / "archive", run_id="run-aliases",
                                   keyword="AI intern", location="Remote", target_count=1,
                                   jobs=[DiscoveredJob(url=url, source="one", source_job_id="1"),
                                         DiscoveredJob(url=url, source="two", source_job_id="2")])
    capture.finalize()
    assert len(capture.archive.read_candidates()) == 2
    assert len(capture.archive.read_manifest().candidate_ids) == 2


@pytest.mark.asyncio
async def test_real_cache_write_receives_returned_archive_references(tmp_path):
    from web_task_agent.source_capture import SourceCaptureBrowser
    url = "https://example.com/jobs/1"
    body = "Title: AI intern\nCompany: Example\nLocation: Remote\nRequirements: Python AI\n" * 3
    job = DiscoveredJob(url=url, title="AI intern", company="Example", source="fixture", source_job_id="1")
    repo = JobRepository(tmp_path / "cache.sqlite")
    repo.initialize()

    async def http(url):
        return BrowserPage(url=url, content=body, source="http")

    loader = AggregatorPageLoader([job], http_loader=http, repository=repo)
    capture = SourceCaptureBrowser(
        BrowserUseClient(page_loader=loader),
        root=tmp_path / "archive",
        run_id="run-cache",
        keyword="AI intern",
        location="Remote",
        target_count=1,
        jobs=[job],
    )
    page = await capture.open_url(url)
    cached = repo.get_cached_page(url)
    assert cached.metadata["candidate_id"] == page.metadata["candidate_id"]
    assert cached.metadata["snapshot_id"] == page.metadata["snapshot_id"]


@pytest.mark.asyncio
async def test_capture_http_status_and_public_operation_group_survive(tmp_path):
    from web_task_agent.browser import PageHttpError
    from web_task_agent.source_capture import SourceCaptureBrowser
    url = "https://example.com/jobs/1"
    body = "Title: AI intern\nCompany: Example\nLocation: Remote\nRequirements: Python AI\n" * 3
    job = DiscoveredJob(url=url, source="fixture", source_job_id="1", jd_text=body)

    class Limited:
        async def fetch(self, url):
            raise PageHttpError("private transport body", 429)

    loader = AggregatorPageLoader([job], official_api=Limited())
    capture = SourceCaptureBrowser(
        BrowserUseClient(page_loader=loader),
        root=tmp_path / "archive",
        run_id="run-status",
        keyword="AI intern",
        location="Remote",
        target_count=1,
        jobs=[job],
    )
    await capture.open_url(url)
    capture.finalize()
    attempts = capture.archive.read_attempts()
    assert attempts[0].http_status == 429
    assert attempts[0].operation_id == attempts[1].operation_id
    assert attempts[0].operation_kind == "open"


@pytest.mark.asyncio
async def test_concurrent_opens_cannot_steal_each_others_attempts(tmp_path):
    import asyncio

    from web_task_agent.source_capture import SourceCaptureBrowser
    jobs = [DiscoveredJob(url=f"https://example.com/jobs/{i}", source="fixture", source_job_id=str(i)) for i in (1, 2)]

    async def http(url):
        await asyncio.sleep(0.01)
        return BrowserPage(url=url, source="http", content="Public AI internship job requirements and duties. " * 4)

    loader = AggregatorPageLoader(jobs, http_loader=http)
    capture = SourceCaptureBrowser(
        BrowserUseClient(page_loader=loader),
        root=tmp_path / "archive",
        run_id="run-concurrent",
        keyword="AI intern",
        location="Remote",
        target_count=2,
        jobs=jobs,
    )
    pages = await asyncio.gather(*(capture.open_url(job.url) for job in jobs))
    capture.finalize()
    attempts = capture.archive.read_attempts()
    assert len(attempts) == 2
    assert {a.candidate_id for a in attempts} == {page.metadata["candidate_id"] for page in pages}
    assert len({a.operation_id for a in attempts}) == 2


@pytest.mark.asyncio
async def test_search_evidence_can_be_replayed_before_any_job_is_opened(tmp_path):
    from web_task_agent.source_capture import SourceCaptureBrowser
    from web_task_agent.source_replay import ArchiveBrowser, MissingSnapshotError
    url = "https://example.com/jobs/1"
    raw = BrowserPage(url="https://www.google.com/search?q=AI", title="Search", content="Public AI job search results",
                      metadata={"candidate_urls": [url]})
    capture = SourceCaptureBrowser(FakeBrowserClient([raw]), root=tmp_path / "archive", run_id="run-search",
                                   keyword="AI", location="Remote", target_count=1)
    await capture.search("AI", 1)
    capture.finalize()
    assert capture.archive.read_attempts()[0].operation_kind == "search"
    replay = ArchiveBrowser(tmp_path / "archive", conditions=capture.conditions)
    pages = await replay.search("AI", 1)
    assert len(pages) == 1
    assert pages[0].url == url
    with pytest.raises(MissingSnapshotError):
        await replay.open_url(url)
