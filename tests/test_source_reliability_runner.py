import json

import pytest

from web_task_agent.browser import PageHttpError
from web_task_agent.job_sources import DiscoveredJob
from web_task_agent.official_list_source import _SPECS
from web_task_agent.streamlit_runner import UiRunRequest, run_ui_request


@pytest.mark.asyncio
async def test_source_failures_reach_ui_and_json_without_search_fallback(tmp_path, monkeypatch):
    async def failed(transport, limit):
        raise TimeoutError("secret request contents")

    monkeypatch.setitem(_SPECS, "test-source", failed)
    async def never_search(url):
        pytest.fail("No discovered jobs must not trigger unrelated web search")

    result = await run_ui_request(
        UiRunRequest(data_mode="official", official_specs=["test-source"]),
        output_root=tmp_path, environ={}, page_loader=never_search,
    )
    assert result.metrics.valid_jobs == 0
    assert result.source_diagnostics[0]["status"] == "failed"
    assert any(row["error"].startswith("source_failed:") for row in result.diagnostics)
    assert "secret request contents" not in json.dumps(result.model_dump(mode="json"))
    payload = json.loads(result.artifacts["json"].read_text(encoding="utf-8"))
    assert payload["source_diagnostics"] == result.source_diagnostics


@pytest.mark.asyncio
async def test_removed_url_reaches_ui_and_json(tmp_path, monkeypatch):
    async def discover(transport, limit):
        return [DiscoveredJob(
            url="https://example.com/jobs/removed", title="AI Agent 实习生", company="Example",
            jd_text="岗位职责：使用 Python LangGraph 开发 AI Agent。任职要求：熟悉大模型工程。" * 5,
        )]

    async def load(url):
        raise PageHttpError("gone", 410)

    monkeypatch.setitem(_SPECS, "test-source", discover)
    result = await run_ui_request(
        UiRunRequest(data_mode="official", official_specs=["test-source"]),
        output_root=tmp_path, environ={}, page_loader=load,
    )
    assert result.metrics.valid_jobs == 0
    assert any(row["error"].startswith("page_removed:") for row in result.diagnostics)
