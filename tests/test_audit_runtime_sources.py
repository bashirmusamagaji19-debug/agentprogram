import asyncio
from datetime import UTC, datetime, timedelta
from time import monotonic

import pytest

from web_task_agent.browser import BrowserConfigurationError, BrowserUseClient, FakeBrowserClient, HttpPageLoader
from web_task_agent.extractor import PageExtractor
from web_task_agent.job_sources import DiscoveredJob
from web_task_agent.models import BrowserPage
from web_task_agent.source_capture import SourceCaptureBrowser
from web_task_agent.source_metrics import build_source_metrics


@pytest.mark.asyncio
async def test_failed_open_counts_in_detail_denominator_and_canonical_has_evidence(tmp_path):
    requested = "https://example.com/1"
    canonical = "https://example.com/canonical/1"

    async def load(url):
        if url.endswith("2"):
            raise TimeoutError("offline")
        return BrowserPage(url=url, content="public body", metadata={"canonical_url": canonical})

    capture = SourceCaptureBrowser(BrowserUseClient(page_loader=load), root=tmp_path / "archive",
        run_id="audit", keyword="AI", location="全国", target_count=2,
        seed_urls=[requested, "https://example.com/2"])
    await capture.open_url(requested)
    with pytest.raises(BrowserConfigurationError):
        await capture.open_url("https://example.com/2")
    capture.finalize()
    summary, evidence = build_source_metrics(capture.archive, delivered_urls=[canonical])
    assert summary["detail_body_fetch"] == {"numerator": 1, "denominator": 2, "value": .5}
    assert evidence[canonical]["snapshot_id"]
    assert evidence[requested] == evidence[canonical]


def test_historical_content_does_not_claim_current_http_verification():
    page = BrowserPage(url="https://example.com/1", content="Title: AI intern\nCompany: Example\nLocation: 北京",
        metadata={"content_origin": "http", "snapshot_content_origin": "cache",
                  "evidence_level": "historical", "availability": "unknown"})
    assert PageExtractor().extract(page).url_verification == "historical"


@pytest.mark.asyncio
async def test_cache_only_is_not_a_detail_request_and_preserves_original_origin(tmp_path):
    from web_task_agent.browser import CachedPageLoader
    from web_task_agent.storage import JobRepository

    url = "https://example.com/1"
    repo = JobRepository(tmp_path / "cache.db")
    repo.initialize()
    repo.cache_page(BrowserPage(url=url, content="public body " * 30, metadata={
        "content_origin": "http", "observed_at": datetime.now(UTC).isoformat()}))

    async def forbidden(url):
        raise AssertionError("cache-only read cannot fetch")

    wrapped = CachedPageLoader(forbidden, repo)
    capture = SourceCaptureBrowser(BrowserUseClient(page_loader=wrapped), root=tmp_path / "archive",
        run_id="audit", keyword="AI", location="全国", target_count=1, seed_urls=[url])
    await capture.open_url(url)
    capture.finalize()
    metrics, evidence = build_source_metrics(capture.archive, delivered_urls=[url])
    assert metrics["detail_body_fetch"]["denominator"] == 0
    assert evidence[url]["content_origin"] == "http"
    assert evidence[url]["acquisition_origin"] == "cache"


def test_cached_loader_propagates_transport_deadline(tmp_path):
    from web_task_agent.browser import CachedPageLoader
    from web_task_agent.storage import JobRepository

    inner = HttpPageLoader()
    wrapped = CachedPageLoader(inner, JobRepository(tmp_path / "cache.db"))
    wrapped.deadline = 123.5
    assert inner.deadline == 123.5


@pytest.mark.asyncio
async def test_visual_cannot_bypass_shared_page_review():
    from web_task_agent.agent_models import DecisionAgentState
    from web_task_agent.agent_tools import ExtractVisualTool
    from web_task_agent.models import UserProfile

    class MustNotRead:
        async def extract(self, page):
            raise AssertionError("visual extraction must be blocked")

    state = DecisionAgentState(user=UserProfile(keyword="AI"), current_page=BrowserPage(
        url="https://example.com/1", content="body",
        metadata={"candidate_binding": "shared_page_requires_review"}))
    observation = await ExtractVisualTool(MustNotRead()).execute(state, {})
    assert observation.success is False
    assert observation.recoverable is False
    assert observation.error_category == "shared_page_requires_review"


@pytest.mark.asyncio
async def test_each_native_candidate_is_bound_and_shared_page_requires_review(tmp_path):
    url = "https://example.com/notice"
    capture = SourceCaptureBrowser(FakeBrowserClient([BrowserPage(url=url, content="Title: AI intern")]),
        root=tmp_path / "archive", run_id="audit", keyword="AI", location="全国", target_count=1,
        jobs=[DiscoveredJob(url=url, source="one", source_job_id="1"),
              DiscoveredJob(url=url, source="two", source_job_id="2")])
    page = await capture.open_url(url)
    capture.finalize()
    bound = {capture.archive.read_page(s).metadata["candidate_id"]
             for s in capture.archive.read_manifest().snapshot_ids}
    assert bound == set(page.metadata["candidate_ids"])
    assert page.metadata["candidate_binding"] == "shared_page_requires_review"
    with pytest.raises(ValueError, match="shared_page_requires_review"):
        PageExtractor().extract(page)
    replayed = capture.archive.read_page(page.metadata["snapshot_id"])
    with pytest.raises(ValueError, match="shared_page_requires_review"):
        PageExtractor().extract(replayed)


@pytest.mark.asyncio
async def test_shared_url_failure_is_recorded_for_each_native_identity(tmp_path):
    url = "https://example.com/unavailable"

    async def fail(url):
        raise TimeoutError("offline")

    capture = SourceCaptureBrowser(BrowserUseClient(page_loader=fail), root=tmp_path / "archive",
        run_id="audit", keyword="AI", location="全国", target_count=1,
        jobs=[DiscoveredJob(url=url, source="one", source_job_id="1"),
              DiscoveredJob(url=url, source="two", source_job_id="2")])
    with pytest.raises(BrowserConfigurationError):
        await capture.open_url(url)
    attempted = {a.candidate_id for a in capture.archive.read_attempts()}
    assert attempted == {c.candidate_id for c in capture.archive.read_candidates()}


@pytest.mark.asyncio
async def test_discovery_has_a_deadline(tmp_path):
    from web_task_agent.agent_components import build_browser_components
    from web_task_agent.agent_service import AgentRunRequest
    from web_task_agent.storage import JobRepository

    class SlowSource:
        def __init__(self, **kwargs):
            pass

        async def discover(self, limit):
            await asyncio.sleep(.3)
            return []

    started = monotonic()
    with pytest.raises(TimeoutError):
        await build_browser_components(AgentRunRequest(data_mode="official", deadline_seconds=.03),
            repository=JobRepository(tmp_path / "jobs.db"), factories={"official": SlowSource})
    assert monotonic() - started < .2


@pytest.mark.asyncio
async def test_archive_identity_work_cannot_block_entrance_deadline(tmp_path, monkeypatch):
    import time

    import web_task_agent.source_capture as capture
    from web_task_agent.agent_components import build_browser_components
    from web_task_agent.agent_service import AgentRunRequest
    from web_task_agent.storage import JobRepository

    def slow_identity():
        time.sleep(.2)
        return "fixture", "0" * 64

    monkeypatch.setattr(capture, "code_identity", slow_identity)
    started = monotonic()
    with pytest.raises(TimeoutError):
        await build_browser_components(AgentRunRequest(data_mode="demo", deadline_seconds=.03),
            repository=JobRepository(tmp_path / "jobs.db"), archive_root=tmp_path / "archive", run_id="audit")
    assert monotonic() - started < .15


@pytest.mark.asyncio
async def test_http_transport_does_not_block_deadline(monkeypatch):
    import time

    def slow(self, url):
        time.sleep(.3)
        return BrowserPage(url=url, content="body")

    monkeypatch.setattr(HttpPageLoader, "_load_sync", slow)
    started = monotonic()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(HttpPageLoader()("https://example.com/1"), timeout=.03)
    assert monotonic() - started < .2


def test_local_retention_removes_only_expired_marked_runs(tmp_path):
    import json

    from web_task_agent.streamlit_runner import cleanup_local_runs

    expired = tmp_path / "run-expired"
    body = expired / "source-archive" / "bodies" / "body.txt"
    body.parent.mkdir(parents=True)
    body.write_text("public body")
    now = datetime.now(UTC)
    (expired / "retention.json").write_text(json.dumps({"run_id": expired.name,
        "retention_deadline": (now - timedelta(days=1)).isoformat()}))
    frozen = tmp_path / "frozen-fixture"
    frozen.mkdir()
    assert cleanup_local_runs(tmp_path, now=now) == 1
    assert not expired.exists()
    assert frozen.exists()


def test_release_lints_all_source_and_tests(tmp_path):
    from types import SimpleNamespace

    from web_task_agent.agent_release_check import run_release_checks
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    run_release_checks(repo_root=tmp_path, runner=runner)
    assert calls[0][-2:] == ["src", "tests"]


def test_official_transport_stops_retrying_after_deadline():
    from web_task_agent.official_list_source import DefaultTransport

    transport = DefaultTransport()
    transport.deadline = monotonic() - 1
    with pytest.raises(TimeoutError, match="deadline"):
        transport.remaining_timeout()


@pytest.mark.asyncio
async def test_service_counts_rejected_verification_in_denominator(tmp_path):
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunOptions, start_agent_run
    from web_task_agent.models import UserProfile

    from .test_agent_multitarget import make_workflow

    workflow, pages = make_workflow(tmp_path, count=2)
    pages[1] = pages[1].model_copy(update={"content": pages[1].content.replace("Remote", "北京")})
    workflow.browser = SourceCaptureBrowser(FakeBrowserClient(pages), root=tmp_path / "archive",
        run_id="audit", keyword="AI intern", location="Remote", target_count=2,
        seed_urls=[p.url for p in pages])
    result = await start_agent_run(UserProfile(keyword="AI intern", location="Remote", target_count=2,
        seed_urls=[p.url for p in pages]), run_id="audit", owner_id="owner",
        services=AgentServices(workflow=workflow, run_dir=tmp_path), options=AgentRunOptions(engine="hybrid"))
    assert result.view.metadata["source_metrics"]["conditions_passed"] == {
        "numerator": 1, "denominator": 2, "value": .5}


@pytest.mark.asyncio
async def test_tiny_business_deadline_still_persists_terminal_checkpoint(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    import web_task_agent.agent_service as service
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.models import UserProfile

    from .test_agent_multitarget import make_workflow

    real_open = service.open_sqlite_checkpointer

    @asynccontextmanager
    async def slow_checkpointer(path):
        async with real_open(path) as saver:
            read = saver.aget_tuple

            async def slow_read(*args, **kwargs):
                await asyncio.sleep(.04)
                return await read(*args, **kwargs)

            saver.aget_tuple = slow_read
            yield saver

    monkeypatch.setattr(service, "open_sqlite_checkpointer", slow_checkpointer)
    workflow, pages = make_workflow(tmp_path, count=1)
    services = AgentServices(workflow=workflow, run_dir=tmp_path)
    result = await service.start_agent_run(UserProfile(keyword="AI intern", location="Remote",
        target_count=1, seed_urls=[pages[0].url]), run_id="audit", owner_id="owner", services=services,
        options=service.AgentRunOptions(engine="hybrid", hitl=True, deadline_seconds=.01))
    assert result.status == "failed"
    assert result.agent_state.terminal_reason == "deadline_exceeded"
    restored = await service.load_agent_run(run_id="audit", owner_id="owner", services=services)
    assert restored.status == "failed"
    assert restored.approval is None
    assert not workflow.repository.list_jobs()
