from __future__ import annotations

import json
from pathlib import Path

from web_task_agent.agent_evaluation import (
    AgentBenchmarkCase,
    AgentFieldGroundTruth,
    build_deterministic_benchmark_cases,
    evaluate_agent_cases,
    render_agent_benchmark_markdown,
    write_agent_benchmark_artifacts,
)
from web_task_agent.agent_models import AgentBudget, AgentMetrics, DecisionAgentState
from web_task_agent.models import JobPosting, UserProfile


def _job(*, title: str = "AI Intern", company: str = "Example", location: str = "Remote"):
    return JobPosting(
        title=title,
        company=company,
        location=location,
        source="fixture",
        url="https://example.com/jobs/1",
        confidence=0.9,
    )


def _state(
    *,
    terminal_status: str = "completed",
    terminal_reason: str = "target_reached",
    jobs: list[JobPosting] | None = None,
    metrics: AgentMetrics | None = None,
    consumed_steps: int = 4,
) -> DecisionAgentState:
    return DecisionAgentState(
        user=UserProfile(keyword="AI intern", target_count=1),
        budget=AgentBudget(max_steps=8, consumed_steps=consumed_steps),
        verified_jobs=jobs or [],
        metrics=metrics or AgentMetrics(),
        terminal_status=terminal_status,
        terminal_reason=terminal_reason,
    )


def test_evaluation_separates_completion_termination_and_field_accuracy():
    completed = AgentBenchmarkCase(
        case_id="target-reached",
        scenario="Target count reached",
        state=_state(
            jobs=[_job()],
            metrics=AgentMetrics(
                tool_calls=4,
                successful_tool_calls=4,
                recovery_attempts=1,
                successful_recoveries=1,
                planner_calls=1,
                total_latency_ms=40,
            ),
        ),
        ground_truth=AgentFieldGroundTruth(
            title="AI Intern", company="Example", location="Remote"
        ),
    )
    exhausted = AgentBenchmarkCase(
        case_id="budget-exhausted",
        scenario="Step budget exhausted",
        state=_state(
            terminal_status="stopped",
            terminal_reason="budget_exhausted",
            metrics=AgentMetrics(
                tool_calls=6,
                successful_tool_calls=3,
                recovery_attempts=2,
                successful_recoveries=1,
                planner_calls=2,
                fallback_decisions=1,
                invalid_actions=1,
                total_latency_ms=80,
            ),
            consumed_steps=6,
        ),
    )

    result = evaluate_agent_cases([completed, exhausted], benchmark_version="test-v1")

    assert result.total_cases == 2
    assert result.completed_cases == 1
    assert result.task_completion_rate == 0.5
    assert result.terminated_cases == 2
    assert result.loop_termination_rate == 1.0
    assert result.tool_success_rate == 0.7
    assert result.recovery_success_rate == 2 / 3
    assert result.invalid_action_rate == 1 / 3
    assert result.fallback_rate == 1 / 3
    assert result.average_steps == 5.0
    assert result.max_steps == 6
    assert result.title_accuracy == 1.0
    assert result.company_accuracy == 1.0
    assert result.location_accuracy == 1.0
    assert result.total_latency_ms == 120
    assert result.provider_calls == 3


def test_field_accuracy_uses_explicit_ground_truth_and_normalized_text():
    case = AgentBenchmarkCase(
        case_id="field-mismatch",
        scenario="Field comparison",
        state=_state(jobs=[_job(title=" ai intern ", location="Shanghai")]),
        ground_truth=AgentFieldGroundTruth(
            title="AI INTERN", company="Different Company", location="Remote"
        ),
    )

    result = evaluate_agent_cases([case])

    assert result.title_accuracy == 1.0
    assert result.company_accuracy == 0.0
    assert result.location_accuracy == 0.0
    assert result.field_accuracy == 1 / 3


def test_render_and_write_benchmark_artifacts_are_explicit_about_scope():
    result = evaluate_agent_cases(
        [
            AgentBenchmarkCase(
                case_id="clean-stop",
                scenario="All candidates fail and terminate cleanly",
                state=_state(
                    terminal_status="stopped",
                    terminal_reason="no_action_available",
                ),
            )
        ],
        benchmark_version="hybrid-agent-deterministic-v1",
    )

    markdown = render_agent_benchmark_markdown(result)
    output_dir = Path("outputs")
    paths = write_agent_benchmark_artifacts(result, output_dir)
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))

    assert "synthetic deterministic scenarios" in markdown
    assert "not extraction accuracy" in markdown
    assert payload["benchmark_version"] == "hybrid-agent-deterministic-v1"
    assert payload["scope"] == "synthetic deterministic scenarios"
    assert paths["markdown"].exists()


def test_deterministic_benchmark_covers_ten_approved_scenarios():
    cases = build_deterministic_benchmark_cases()
    result = evaluate_agent_cases(cases)

    assert len(cases) == 10
    assert result.completed_cases == 8
    assert result.terminated_cases == 10
    assert result.provider_calls == 2
    assert {case.case_id for case in cases} == {
        "happy-path",
        "search-filtering",
        "open-recovery",
        "visual-recovery",
        "unsupported-tool",
        "invalid-json",
        "verifier-recovery",
        "all-candidates-fail",
        "budget-exhausted",
        "target-reached",
    }
