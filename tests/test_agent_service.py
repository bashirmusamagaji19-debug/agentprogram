from __future__ import annotations

import pytest
from pydantic import ValidationError

from web_task_agent.models import UserProfile

from .test_agent_multitarget import make_workflow


def test_discovered_candidate_budget_preserves_explicit_override():
    from web_task_agent.agent_service import AgentRunRequest
    request = AgentRunRequest(target_count=1)
    assert request.options(candidate_count=8).max_steps == 36
    assert request.options(candidate_count=20).max_steps == 44
    assert request.model_copy(update={'max_steps': 7}).options(candidate_count=20).max_steps == 7


@pytest.mark.asyncio
async def test_candidate_budget_reaches_late_valid_job_after_rejections(tmp_path):
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunRequest, start_agent_run
    from web_task_agent.browser import FakeBrowserClient

    workflow, pages = make_workflow(tmp_path, count=8)
    for page in pages[:-1]:
        page.content = page.content.replace('Location: Remote', 'Location: 北京')
    workflow.browser = FakeBrowserClient(pages)
    request = AgentRunRequest(engine='hybrid', target_count=1, keyword='AI intern', location='Remote',
                              seed_urls=[p.url for p in pages])
    result = await start_agent_run(
        request, run_id='late-valid', owner_id='owner',
        services=AgentServices(workflow=workflow, run_dir=tmp_path),
        options=request.options(candidate_count=len(pages)),
    )
    assert result.status == 'succeeded'
    assert result.view.jobs[0].url == pages[-1].url
    assert len(workflow.repository.list_jobs()) == 1


@pytest.mark.asyncio
async def test_three_remaining_steps_match_save_and_deliver_partial(tmp_path):
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunOptions, start_agent_run

    workflow, pages = make_workflow(tmp_path, count=3)
    result = await start_agent_run(
        UserProfile(keyword='AI intern', location='Remote', target_count=3,
                    seed_urls=[p.url for p in pages]),
        run_id='partial-budget', owner_id='owner',
        services=AgentServices(workflow=workflow, run_dir=tmp_path),
        options=AgentRunOptions(engine='hybrid', max_steps=6),
    )
    assert result.status == 'partial'
    assert len(result.view.jobs) == 1
    assert len(result.view.matches) == 1
    assert len(workflow.repository.list_jobs()) == 1
    assert result.agent_state.saved


@pytest.mark.asyncio
async def test_small_real_inventory_is_not_mislabeled_as_budget_exhaustion(tmp_path):
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunRequest, start_agent_run

    workflow, pages = make_workflow(tmp_path, count=1)
    request = AgentRunRequest(engine='hybrid', keyword='AI intern', location='Remote', target_count=3,
                              seed_urls=[pages[0].url])
    result = await start_agent_run(request, run_id='small-inventory', owner_id='owner',
                                   services=AgentServices(workflow=workflow, run_dir=tmp_path),
                                   options=request.options(candidate_count=1))
    assert result.status == 'partial'
    assert result.agent_state.terminal_reason == 'candidates_exhausted'
    assert result.agent_state.saved


@pytest.mark.asyncio
@pytest.mark.parametrize('target', [1, 3])
async def test_last_available_step_saves_verified_jobs_without_claiming_matching_complete(tmp_path, target):
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunOptions, start_agent_run

    workflow, pages = make_workflow(tmp_path, count=1)
    result = await start_agent_run(
        UserProfile(keyword='AI intern', location='Remote', target_count=target, seed_urls=[pages[0].url]),
        run_id='last-step', owner_id='owner', services=AgentServices(workflow=workflow, run_dir=tmp_path),
        options=AgentRunOptions(engine='hybrid', max_steps=4),
    )
    assert result.status == 'partial'
    assert result.agent_state.saved
    assert len(workflow.repository.list_jobs()) == 1
    assert result.agent_state.budget.consumed_steps == 4


@pytest.mark.asyncio
async def test_seed_url_reuses_official_api_before_spa_http(tmp_path):
    from web_task_agent.agent_components import build_browser_components
    from web_task_agent.agent_service import AgentRunRequest
    from web_task_agent.official_api import OfficialApiContent

    workflow, pages = make_workflow(tmp_path, count=1)

    class Api:
        async def fetch(self, url):
            return OfficialApiContent(content=pages[0].content, title=pages[0].title)

    async def spa_loader(url):
        pytest.fail('official supported URLs should use the API before SPA HTTP')

    browser, urls = await build_browser_components(
        AgentRunRequest(data_mode='seed_urls', seed_urls=[pages[0].url]),
        repository=workflow.repository, page_loader=spa_loader, factories={'official_api': Api},
    )
    page = await browser.open_url(urls[0])
    assert 'Python' in page.content
    assert page.metadata['content_origin'] == 'official-api'


def test_options_require_provider_for_model_strategy_and_hybrid_for_hitl():
    from web_task_agent.agent_service import AgentRunOptions
    with pytest.raises(ValidationError):
        AgentRunOptions(engine="workflow", hitl=True)
    with pytest.raises(ValidationError):
        AgentRunOptions(engine="hybrid", planner_strategy="always")
    assert AgentRunOptions(engine="hybrid").planner_strategy == "policy"


@pytest.mark.asyncio
async def test_service_runs_real_hybrid_and_projects_without_duplicate_save(tmp_path):
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunOptions, start_agent_run
    workflow, pages = make_workflow(tmp_path)
    user = UserProfile(keyword="AI intern", location="Remote", target_count=3,
                       seed_urls=[page.url for page in pages])
    saved = []
    real_save = workflow.repository.save_jobs_once

    def save_once(jobs, *, idempotency_key):
        saved.append(idempotency_key)
        return real_save(jobs, idempotency_key=idempotency_key)

    workflow.repository.save_jobs_once = save_once
    result = await start_agent_run(
        user, run_id="run-test", owner_id="owner-1",
        services=AgentServices(workflow=workflow, run_dir=tmp_path),
        options=AgentRunOptions(engine="hybrid", max_steps=30),
    )
    assert result.status == "succeeded"
    assert result.agent_state.terminal_reason == "target_reached"
    assert len(saved) == 1
    assert len(workflow.repository.list_jobs()) == 3
    assert len(result.view.jobs) == 3
    assert result.view.metrics.run_id == "run-test"
    assert result.view.metadata["orchestration_mode"] == "hybrid"
    assert result.view.metadata["agent_trace"]
    assert result.view.report_path
    trace = result.view.metadata["agent_trace"]
    assert trace[0]["remaining_budget"] == 29
    assert trace[-1]["remaining_budget"] == result.agent_state.budget.remaining_steps
    assert all(isinstance(row["planner_usage"], list) for row in trace)
    assert result.view.metadata["open_successes"] == 3
    assert result.view.metadata["open_failures"] == 0
    assert result.view.metadata["unique_candidates"] == 3
    assert result.view.metadata["runtime_latency_ms"] >= result.view.metadata["tool_latency_ms"] >= 0
    assert result.view.metrics.estimated_token_cost is None
    report = __import__("pathlib").Path(result.view.report_path).read_text(encoding="utf-8")
    assert "target_reached" in report
    assert "completed" in report
    assert "Token" in report


@pytest.mark.asyncio
async def test_policy_strategy_never_calls_injected_planner(tmp_path):
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunOptions, start_agent_run

    class ForbiddenPlanner:
        async def decide(self, state):
            pytest.fail("policy must not call a planner")

    workflow, pages = make_workflow(tmp_path, count=1)
    result = await start_agent_run(
        UserProfile(keyword="AI intern", location="Remote", target_count=1, seed_urls=[pages[0].url]),
        run_id="run-policy-only", owner_id="owner",
        services=AgentServices(workflow=workflow, run_dir=tmp_path, planner=ForbiddenPlanner()),
        options=AgentRunOptions(engine="hybrid", planner_strategy="policy"),
    )
    assert result.status == "succeeded"
    assert result.agent_state.metrics.planner_calls == 0


@pytest.mark.asyncio
async def test_service_keeps_workflow_as_explicit_baseline(tmp_path):
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunOptions, start_agent_run
    workflow, pages = make_workflow(tmp_path, count=1)
    result = await start_agent_run(
        UserProfile(keyword="AI intern", location="Remote", target_count=1,
                    seed_urls=[pages[0].url]),
        run_id="run-baseline", owner_id="owner-1",
        services=AgentServices(workflow=workflow, run_dir=tmp_path),
        options=AgentRunOptions(engine="workflow"),
    )
    assert result.agent_state is None
    assert result.view.metadata["orchestration_mode"] == "langgraph"
    assert len(result.view.jobs) == 1


@pytest.mark.asyncio
async def test_ui_hybrid_request_uses_shared_service(tmp_path, monkeypatch):
    import web_task_agent.streamlit_runner as runner
    from web_task_agent.agent_service import start_agent_run
    calls = []

    async def spy(*args, **kwargs):
        calls.append(kwargs["options"].engine)
        return await start_agent_run(*args, **kwargs)

    monkeypatch.setattr(runner, "start_agent_run", spy)
    result = await runner.run_ui_request(
        runner.UiRunRequest(keyword="AI intern", location="全国", target_count=2, engine="hybrid"),
        output_root=tmp_path, environ={},
    )
    assert calls == ["hybrid"]
    assert result.engine == "hybrid"
    assert result.terminal_status == "completed"
    assert len(result.jobs) == 2
    assert result.agent_trace


@pytest.mark.asyncio
async def test_shared_application_service_enforces_conditions_for_legacy_workflow(tmp_path):
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunOptions, start_agent_run
    workflow, pages = make_workflow(tmp_path, count=1)
    workflow.enforce_search_conditions = False
    result = await start_agent_run(
        UserProfile(keyword="AI intern", location="北京", target_count=1, seed_urls=[pages[0].url]),
        run_id="run-strict", owner_id="owner", services=AgentServices(workflow=workflow, run_dir=tmp_path),
        options=AgentRunOptions(engine="hybrid"),
    )
    assert not result.view.jobs
    assert not workflow.repository.list_jobs()


@pytest.mark.asyncio
async def test_cli_hybrid_uses_shared_application_service(tmp_path, monkeypatch):
    import web_task_agent.cli as cli
    from web_task_agent.agent_service import start_agent_run
    calls = []

    async def spy(*args, **kwargs):
        calls.append(kwargs["options"].engine)
        return await start_agent_run(*args, **kwargs)

    monkeypatch.setattr(cli, "start_agent_run", spy)
    monkeypatch.setattr(cli, "write_hybrid_artifacts", lambda *args, **kwargs: {})
    args = cli.build_parser().parse_args([
        "--demo", "--hybrid-agent", "--keyword", "AI intern", "--target-count", "1",
        "--db-path", str(tmp_path / "jobs.sqlite"), "--report-dir", str(tmp_path / "reports"),
    ])
    assert await cli._run(args) == 0
    assert calls == ["hybrid"]


@pytest.mark.asyncio
async def test_deadline_without_saving_returns_failed_with_undelivered_candidates(tmp_path):
    import asyncio

    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunOptions, start_agent_run
    workflow, pages = make_workflow(tmp_path, count=2)

    class SlowSecondPage:
        async def open_url(self, url):
            if url == pages[1].url:
                await asyncio.sleep(0.3)
            return next(page for page in pages if page.url == url)

    workflow.browser = SlowSecondPage()
    result = await start_agent_run(
        UserProfile(keyword="AI intern", location="Remote", target_count=2, seed_urls=[p.url for p in pages]),
        run_id="run-timeout", owner_id="owner", services=AgentServices(workflow=workflow, run_dir=tmp_path),
        options=AgentRunOptions(engine="hybrid", deadline_seconds=0.1),
    )
    assert result.status == "failed"
    assert result.agent_state.terminal_reason == "deadline_exceeded"
    assert result.view.jobs == []
    assert len(result.view.pending_jobs) == 1
    assert not workflow.repository.list_jobs()


def test_workflow_options_reject_planner_strategy_that_cannot_be_executed():
    from web_task_agent.agent_service import AgentRunOptions
    with pytest.raises(ValidationError):
        AgentRunOptions(engine="workflow", planner_strategy="adaptive", planner_provider="qwen")


@pytest.mark.asyncio
async def test_cli_artifacts_share_one_run_id_and_one_report(tmp_path):
    import json

    import web_task_agent.cli as cli
    reports, dashboards = tmp_path / "reports", tmp_path / "dashboards"
    output = tmp_path / "result.json"
    args = cli.build_parser().parse_args([
        "--demo", "--hybrid-agent", "--keyword", "AI intern", "--target-count", "1",
        "--db-path", str(tmp_path / "jobs.sqlite"), "--report-dir", str(reports),
        "--dashboard-dir", str(dashboards), "--dashboard", "--json-output", str(output),
    ])
    assert await cli._run(args) == 0
    report_files = list(reports.glob("*.md"))
    assert len(report_files) == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["run_id"] == report_files[0].stem
    assert (dashboards / f"{payload['run_id']}.html").is_file()
