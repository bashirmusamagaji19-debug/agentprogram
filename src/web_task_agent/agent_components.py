from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any

from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.agent_runtime import HybridAgentRuntime
from web_task_agent.agent_tools import (
    AgentToolRegistry,
    ExtractTextTool,
    ExtractVisualTool,
    FinishTool,
    OpenPageTool,
    SaveResultsTool,
    ScoreMatchTool,
    SearchJobsTool,
    VerifyJobTool,
)

if TYPE_CHECKING:
    from web_task_agent.workflow import WebTaskWorkflow


@dataclass
class AgentServices:
    workflow: WebTaskWorkflow
    run_dir: Path
    planner: Any = None
    checkpoint_path: Path | None = None
    thread_id: str | None = None
    model_budget: Any = None


def build_hybrid_runtime(workflow, *, planner=None, checkpointer=None, **runtime_options) -> HybridAgentRuntime:
    from web_task_agent.link_verification import JobLinkVerifier

    require_links = getattr(workflow, "require_verified_links", False)
    checker = getattr(workflow, "link_verifier", None)
    if require_links and checker is None:
        checker = JobLinkVerifier(deadline=getattr(workflow.browser, "request_deadline", None),
                                  evidence_dir=workflow.reporter.output_dir.parent / "link-checks")
    tools = [
        SearchJobsTool(workflow.browser), OpenPageTool(workflow.browser),
        ExtractTextTool(workflow.extractor),
        VerifyJobTool(workflow.verifier,
                      enforce_search_conditions=getattr(workflow, "enforce_search_conditions", False),
                      link_verifier=checker, require_verified_links=require_links),
        ScoreMatchTool(workflow.matcher),
        SaveResultsTool(workflow.repository, require_verified_links=require_links), FinishTool(),
    ]
    if workflow.visual_extractor is not None:
        tools.append(ExtractVisualTool(workflow.visual_extractor))
    return HybridAgentRuntime(
        registry=AgentToolRegistry(tools), policy=DeterministicAgentPolicy(),
        planner=planner, checkpointer=checkpointer, **runtime_options,
    )


async def _build_browser_components(request, *, repository, page_loader=None,
                                   aggregator_location=None, factories=None,
                                   archive_root=None, run_id=None, demo_pages=None, request_deadline=None,
                                   candidate_limit=None):
    """Shared source/browser construction with injectable adapter factories."""
    from web_task_agent.browser import BrowserUseClient, FakeBrowserClient, HttpPageLoader
    from web_task_agent.demo_pages import DEMO_JOB_PAGES
    from web_task_agent.extractor import PageExtractor
    from web_task_agent.job_sources import AggregatorPageLoader, AggregatorRepoSource, DiscoveredJob
    from web_task_agent.official_api import OfficialApiContentFetcher
    from web_task_agent.official_list_source import OfficialListSource
    from web_task_agent.search_conditions import candidate_matches
    from web_task_agent.source_capture import SourceCaptureBrowser

    adapters = {"browser": BrowserUseClient, "fake": FakeBrowserClient, "http": HttpPageLoader,
                "aggregator": AggregatorRepoSource, "official": OfficialListSource,
                "official_api": OfficialApiContentFetcher, "loader": AggregatorPageLoader}
    adapters.update(factories or {})
    http_loader = page_loader
    if request.data_mode != "demo" and http_loader is None:
        http_loader = adapters["http"]()
    if hasattr(http_loader, "deadline"):
        http_loader.deadline = request_deadline
    jobs = []
    if request.data_mode == "demo":
        extractor = PageExtractor()
        pages = [page for page in (DEMO_JOB_PAGES if demo_pages is None else demo_pages) if candidate_matches(
            request.keyword, request.location, title=page.title, jd_text=page.content,
            location=extractor.extract(page).location)]
        browser, seeds = adapters["fake"](pages), []
    elif request.data_mode == "seed_urls":
        seeds = request.seed_urls
        jobs = [DiscoveredJob(url=url, source="seed_urls") for url in dict.fromkeys(seeds)]
        official_api = adapters["official_api"]()
        if isinstance(official_api, OfficialApiContentFetcher):
            official_api.deadline = request_deadline
        loader = adapters["loader"](jobs, official_api=official_api,
                                    http_loader=http_loader, repository=repository)
        browser = adapters["browser"](page_loader=loader, discovery_only=True)
    else:
        source = (adapters["official"](specs=request.official_specs or None,
                                      keyword=request.keyword, location=request.location)
                  if request.data_mode == "official" else adapters["aggregator"](
                      aggregator_location or request.aggregator_path or request.aggregator_url,
                      keyword=request.keyword, location=request.location))
        if request.data_mode == "official" and hasattr(source, "transport"):
            source.transport.deadline = request_deadline
        elif hasattr(source, "deadline"):
            source.deadline = request_deadline
        # Existing list adapters contain synchronous HTTP transports. Keep them
        # off the UI event loop so the entrance deadline remains effective.
        jobs = await asyncio.to_thread(
            lambda: asyncio.run(source.discover(limit=min(100, max(20, request.target_count * 3)))))
        discovered_count = len(jobs)
        if candidate_limit is not None:
            jobs = jobs[:candidate_limit]
        official_api = adapters["official_api"]()
        if isinstance(official_api, OfficialApiContentFetcher):
            official_api.deadline = request_deadline
        loader = adapters["loader"](jobs, official_api=official_api,
                                    http_loader=http_loader, repository=repository)
        loader.source_diagnostics = list(getattr(source, "diagnostics", []))
        browser = adapters["browser"](page_loader=loader, discovery_only=True)
        browser.discovered_count = discovered_count
        seeds = [job.url for job in jobs]
    if request.data_mode == "seed_urls":
        browser.discovered_count = len(seeds)
        seeds = list(dict.fromkeys(seeds))
        if candidate_limit is not None:
            seeds = seeds[:candidate_limit]
    elif request.data_mode == "demo":
        browser.discovered_count = len(pages)
    discovered_count = getattr(browser, "discovered_count", len(seeds))
    if archive_root is not None:
        browser = await asyncio.to_thread(
            SourceCaptureBrowser, browser, root=archive_root, run_id=run_id,
            keyword=request.keyword, location=request.location,
            target_count=request.target_count, jobs=jobs,
            seed_urls=seeds, mode=request.data_mode)
    browser.discovered_count = discovered_count
    browser.require_verified_links = request.data_mode != "demo"
    return browser, seeds


async def build_browser_components(request, **kwargs):
    deadline = monotonic() + request.deadline_seconds
    browser, seeds = await asyncio.wait_for(
        _build_browser_components(request, request_deadline=deadline, **kwargs), timeout=request.deadline_seconds)
    browser.request_deadline = deadline
    return browser, seeds
