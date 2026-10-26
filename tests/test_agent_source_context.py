import json

import pytest

from web_task_agent.agent_approval import ApprovalDecision
from web_task_agent.agent_run_context import read_run_context
from web_task_agent.streamlit_runner import UiRunRequest, resume_ui_request, run_ui_request


@pytest.mark.asyncio
async def test_source_context_survives_approval_with_single_report(tmp_path):
    pending = await run_ui_request(
        UiRunRequest(keyword="AI intern", location="全国", target_count=2, engine="hybrid", hitl=True),
        output_root=tmp_path, owner_id="owner", environ={},
    )
    root = tmp_path / pending.run_id
    context = read_run_context(root, run_id=pending.run_id, owner_id="owner")
    assert context.source_manifest == pending.source_manifest
    assert len(context.candidate_ids) >= 2
    assert context.source_mode == "demo"
    resumed = await resume_ui_request(
        pending.run_id, ApprovalDecision(approval_id=pending.approval["approval_id"], outcome="approve"),
        output_root=tmp_path, owner_id="owner", environ={},
    )
    assert resumed.source_manifest == pending.source_manifest
    report = resumed.artifacts["report"].read_text(encoding="utf-8")
    assert pending.source_manifest in report
    assert "demo" in report
    assert len(list((root / "reports").glob("*.md"))) == 1
    payload = json.loads(resumed.artifacts["json"].read_text(encoding="utf-8"))
    assert payload["source_manifest"] == pending.source_manifest


@pytest.mark.asyncio
async def test_auxiliary_model_without_usage_is_never_reported_as_zero_tokens(tmp_path):
    from web_task_agent.agent_components import AgentServices
    from web_task_agent.agent_service import AgentRunOptions, start_agent_run
    from web_task_agent.matcher import JobMatcher
    from web_task_agent.models import UserProfile

    from .test_agent_multitarget import make_workflow

    workflow, pages = make_workflow(tmp_path, count=1)
    calls = []

    def model_match(payload):
        calls.append(True)
        return {"score": 0.5, "reason": "injected model"}

    workflow.matcher = JobMatcher(llm_matcher=model_match)
    result = await start_agent_run(
        UserProfile(keyword="AI intern", location="Remote", target_count=1, seed_urls=[pages[0].url]),
        run_id="run-unknown-usage", owner_id="owner", services=AgentServices(workflow=workflow, run_dir=tmp_path),
        options=AgentRunOptions(engine="hybrid"),
    )
    assert calls
    assert result.view.metadata["model_usage_complete"] is False
    report = __import__("pathlib").Path(result.view.report_path).read_text(encoding="utf-8")
    assert "模型 Token: 未知" in report
    assert "模型 Token: 0" not in report
