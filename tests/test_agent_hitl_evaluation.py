import json

import pytest

from web_task_agent.agent_hitl_evaluation import (
    HitlCaseResult,
    evaluate_hitl_cases,
    render_hitl_evaluation_markdown,
    run_hitl_evaluation,
    write_hitl_evaluation_artifacts,
)


def test_hitl_evaluation_reports_protected_effects():
    result = evaluate_hitl_cases(
        [
            HitlCaseResult(
                case_id="approve",
                paused=True,
                approved=True,
                saved_effects=1,
            ),
            HitlCaseResult(
                case_id="reject",
                paused=True,
                rejected=True,
                saved_effects=0,
            ),
            HitlCaseResult(
                case_id="replay",
                paused=True,
                approved=True,
                saved_effects=1,
                replayed=True,
            ),
        ]
    )

    assert result.benchmark_version == "hybrid-agent-hitl-v1"
    assert result.pause_rate == 1.0
    assert result.rejected_effects == 0
    assert result.duplicate_effects == 0


def test_hitl_evaluation_writes_versioned_redacted_artifacts(tmp_path):
    result = evaluate_hitl_cases(
        [
            HitlCaseResult(
                case_id="reject",
                paused=True,
                rejected=True,
                saved_effects=0,
            )
        ]
    )

    markdown = render_hitl_evaluation_markdown(result)
    paths = write_hitl_evaluation_artifacts(result, tmp_path)
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))

    assert "deterministic checkpoint" in markdown
    assert "does not measure real-site extraction" in markdown
    assert payload["benchmark_version"] == "hybrid-agent-hitl-v1"
    assert paths["markdown"].read_text(encoding="utf-8") == markdown
    serialized = json.dumps(payload)
    assert "Authorization" not in serialized
    assert "api_key" not in serialized


@pytest.mark.asyncio
async def test_run_hitl_evaluation_executes_real_checkpoint_scenarios(tmp_path):
    result = await run_hitl_evaluation(tmp_path)

    assert [case.case_id for case in result.cases] == [
        "approve",
        "reject",
        "replay",
    ]
    assert result.pause_rate == 1.0
    assert result.rejected_effects == 0
    assert result.duplicate_effects == 0
    assert result.cases[0].terminal_reason == "target_reached"
    assert result.cases[1].terminal_reason == "human_denied"
