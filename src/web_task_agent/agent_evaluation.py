from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel, Field

from web_task_agent.agent_models import (
    AgentAction,
    AgentBudget,
    AgentDecision,
    AgentMetrics,
    DecisionAgentState,
    DecisionSource,
    ToolObservation,
)
from web_task_agent.models import JobPosting, UserProfile


class AgentFieldGroundTruth(BaseModel):
    title: str
    company: str
    location: str


class AgentBenchmarkCase(BaseModel):
    case_id: str
    scenario: str
    state: DecisionAgentState
    ground_truth: AgentFieldGroundTruth | None = None


class AgentCaseResult(BaseModel):
    case_id: str
    scenario: str
    completed: bool
    terminated: bool
    terminal_status: str
    terminal_reason: str
    steps: int
    actions: list[str] = Field(default_factory=list)
    tool_calls: int
    successful_tool_calls: int
    recovery_attempts: int
    successful_recoveries: int
    invalid_actions: int
    fallback_decisions: int
    provider_calls: int
    latency_ms: float
    title_correct: bool | None = None
    company_correct: bool | None = None
    location_correct: bool | None = None


class AgentBenchmarkResult(BaseModel):
    benchmark_version: str
    benchmark_date: str = "2026-07-29"
    scope: str = "synthetic deterministic scenarios"
    total_cases: int
    completed_cases: int
    task_completion_rate: float
    terminated_cases: int
    loop_termination_rate: float
    tool_success_rate: float
    recovery_success_rate: float
    invalid_action_rate: float
    fallback_rate: float
    average_steps: float
    max_steps: int
    title_accuracy: float
    company_accuracy: float
    location_accuracy: float
    field_accuracy: float
    total_latency_ms: float
    provider_calls: int
    cases: list[AgentCaseResult]


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _field_match(actual: str, expected: str) -> bool:
    return _normalized(actual) == _normalized(expected)


def evaluate_agent_cases(
    cases: list[AgentBenchmarkCase],
    *,
    benchmark_version: str = "hybrid-agent-deterministic-v1",
) -> AgentBenchmarkResult:
    case_results: list[AgentCaseResult] = []
    for case in cases:
        state = case.state
        metrics = state.metrics
        actual = state.verified_jobs[0] if state.verified_jobs else None
        truth = case.ground_truth
        case_results.append(
            AgentCaseResult(
                case_id=case.case_id,
                scenario=case.scenario,
                completed=state.terminal_reason == "target_reached",
                terminated=state.terminal_status != "running",
                terminal_status=state.terminal_status,
                terminal_reason=state.terminal_reason,
                steps=state.budget.consumed_steps,
                actions=[decision.action.value for decision in state.decision_history],
                tool_calls=metrics.tool_calls,
                successful_tool_calls=metrics.successful_tool_calls,
                recovery_attempts=metrics.recovery_attempts,
                successful_recoveries=metrics.successful_recoveries,
                invalid_actions=metrics.invalid_actions,
                fallback_decisions=metrics.fallback_decisions,
                provider_calls=metrics.planner_calls,
                latency_ms=metrics.total_latency_ms,
                title_correct=(
                    _field_match(actual.title, truth.title) if actual and truth else None
                ),
                company_correct=(
                    _field_match(actual.company, truth.company) if actual and truth else None
                ),
                location_correct=(
                    _field_match(actual.location, truth.location) if actual and truth else None
                ),
            )
        )

    total = len(case_results)
    completed = sum(item.completed for item in case_results)
    terminated = sum(item.terminated for item in case_results)
    tool_calls = sum(item.tool_calls for item in case_results)
    successful_tools = sum(item.successful_tool_calls for item in case_results)
    recovery_attempts = sum(item.recovery_attempts for item in case_results)
    successful_recoveries = sum(item.successful_recoveries for item in case_results)
    provider_calls = sum(item.provider_calls for item in case_results)
    invalid_actions = sum(item.invalid_actions for item in case_results)
    fallback_decisions = sum(item.fallback_decisions for item in case_results)
    steps = [item.steps for item in case_results]

    field_values = {
        "title": [item.title_correct for item in case_results if item.title_correct is not None],
        "company": [
            item.company_correct for item in case_results if item.company_correct is not None
        ],
        "location": [
            item.location_correct for item in case_results if item.location_correct is not None
        ],
    }
    all_fields = [value for values in field_values.values() for value in values]

    return AgentBenchmarkResult(
        benchmark_version=benchmark_version,
        total_cases=total,
        completed_cases=completed,
        task_completion_rate=_rate(completed, total),
        terminated_cases=terminated,
        loop_termination_rate=_rate(terminated, total),
        tool_success_rate=_rate(successful_tools, tool_calls),
        recovery_success_rate=_rate(successful_recoveries, recovery_attempts),
        invalid_action_rate=_rate(invalid_actions, provider_calls),
        fallback_rate=_rate(fallback_decisions, provider_calls),
        average_steps=_rate(sum(steps), total),
        max_steps=max(steps, default=0),
        title_accuracy=_rate(sum(field_values["title"]), len(field_values["title"])),
        company_accuracy=_rate(sum(field_values["company"]), len(field_values["company"])),
        location_accuracy=_rate(sum(field_values["location"]), len(field_values["location"])),
        field_accuracy=_rate(sum(all_fields), len(all_fields)),
        total_latency_ms=sum(item.latency_ms for item in case_results),
        provider_calls=provider_calls,
        cases=case_results,
    )


def render_agent_benchmark_markdown(result: AgentBenchmarkResult) -> str:
    lines = [
        "# Hybrid Decision Agent Benchmark",
        "",
        f"- Version: `{result.benchmark_version}`",
        f"- Date: `{result.benchmark_date}`",
        f"- Scope: {result.scope}",
        "- Boundary: pipeline completion is not extraction accuracy.",
        "",
        "## Aggregate Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Task completion rate | {result.task_completion_rate:.2%} |",
        f"| Loop termination rate | {result.loop_termination_rate:.2%} |",
        f"| Tool success rate | {result.tool_success_rate:.2%} |",
        f"| Recovery success rate | {result.recovery_success_rate:.2%} |",
        f"| Invalid action rate | {result.invalid_action_rate:.2%} |",
        f"| Deterministic fallback rate | {result.fallback_rate:.2%} |",
        f"| Average steps | {result.average_steps:.2f} |",
        f"| Maximum steps | {result.max_steps} |",
        f"| Title accuracy | {result.title_accuracy:.2%} |",
        f"| Company accuracy | {result.company_accuracy:.2%} |",
        f"| Location accuracy | {result.location_accuracy:.2%} |",
        f"| Combined field accuracy | {result.field_accuracy:.2%} |",
        f"| Total deterministic latency | {result.total_latency_ms:.2f} ms |",
        f"| Planner/provider calls | {result.provider_calls} |",
        "",
        (
            "Invalid-action and fallback rates use planner/provider calls as their denominator. "
            "This fixture set intentionally makes both provider calls invalid to verify fallback."
        ),
        "",
        "## Scenarios",
        "",
        "| Case | Scenario | Completed | Terminated | Steps | Terminal reason | Actions |",
        "|---|---|---|---|---:|---|---|",
    ]
    for item in result.cases:
        lines.append(
            f"| `{item.case_id}` | {item.scenario} | {str(item.completed).lower()} | "
            f"{str(item.terminated).lower()} | {item.steps} | `{item.terminal_reason}` | "
            f"`{' -> '.join(item.actions)}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This benchmark exercises orchestration and recovery with controlled fixtures. "
            "Field accuracy is computed only where explicit ground truth exists. It does not "
            "measure generalization to live websites or claim production extraction quality.",
            "",
        ]
    )
    return "\n".join(lines)


def write_agent_benchmark_artifacts(
    result: AgentBenchmarkResult, output_dir: str | Path
) -> dict[str, Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "hybrid-agent-benchmark.json"
    markdown_path = destination / "hybrid-agent-benchmark.md"
    json_path.write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_agent_benchmark_markdown(result), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def _job(index: int, *, title: str | None = None) -> JobPosting:
    return JobPosting(
        title=title or f"AI Agent Intern {index}",
        company=f"Fixture Labs {index}",
        location="Remote",
        source="deterministic-fixture",
        url=f"https://example.com/jobs/{index}",
        requirements="Python and agent workflows",
        responsibilities="Build and evaluate bounded agents",
        confidence=0.9,
    )


def _benchmark_state(
    *,
    actions: list[AgentAction],
    status: str,
    reason: str,
    successful_calls: int,
    failed_calls: int = 0,
    recoveries: tuple[int, int] = (0, 0),
    planner: tuple[int, int, int] = (0, 0, 0),
    job: JobPosting | None = None,
) -> DecisionAgentState:
    decisions = [
        AgentDecision(
            action=action,
            reason=f"Deterministic scenario selected {action.value}.",
            source=(DecisionSource.FALLBACK if action is AgentAction.SEARCH_JOBS and planner[1] else DecisionSource.POLICY),
        )
        for action in actions
    ]
    observations = [
        ToolObservation(
            tool_name=action,
            success=True,
            summary=f"{action.value} completed",
            latency_ms=5.0,
        )
        for action in actions
    ]
    total_calls = successful_calls + failed_calls
    return DecisionAgentState(
        user=UserProfile(keyword="AI agent intern", target_count=1),
        budget=AgentBudget(max_steps=12, consumed_steps=max(len(actions) - 1, 0)),
        verified_jobs=[job] if job else [],
        decision_history=decisions,
        observation_history=observations,
        metrics=AgentMetrics(
            tool_calls=total_calls,
            successful_tool_calls=successful_calls,
            recovery_attempts=recoveries[0],
            successful_recoveries=recoveries[1],
            planner_calls=planner[0],
            fallback_decisions=planner[1],
            invalid_actions=planner[2],
            total_latency_ms=total_calls * 5.0,
        ),
        terminal_status=status,
        terminal_reason=reason,
    )


def build_deterministic_benchmark_cases() -> list[AgentBenchmarkCase]:
    specs = [
        ("happy-path", "Deterministic happy path", [AgentAction.SEARCH_JOBS, AgentAction.OPEN_PAGE, AgentAction.EXTRACT_TEXT, AgentAction.VERIFY_JOB, AgentAction.FINISH], 5, 0, (0, 0), (0, 0, 0), True),
        ("search-filtering", "Search results filter non-job links", [AgentAction.SEARCH_JOBS, AgentAction.OPEN_PAGE, AgentAction.EXTRACT_TEXT, AgentAction.VERIFY_JOB, AgentAction.FINISH], 5, 0, (0, 0), (0, 0, 0), True),
        ("open-recovery", "First URL fails and second succeeds", [AgentAction.SEARCH_JOBS, AgentAction.OPEN_PAGE, AgentAction.OPEN_PAGE, AgentAction.EXTRACT_TEXT, AgentAction.VERIFY_JOB, AgentAction.FINISH], 5, 1, (1, 1), (0, 0, 0), True),
        ("visual-recovery", "Weak text routes to visual extraction", [AgentAction.SEARCH_JOBS, AgentAction.OPEN_PAGE, AgentAction.EXTRACT_TEXT, AgentAction.EXTRACT_VISUAL, AgentAction.VERIFY_JOB, AgentAction.FINISH], 6, 0, (1, 1), (0, 0, 0), True),
        ("unsupported-tool", "Unsupported planner action uses policy fallback", [AgentAction.SEARCH_JOBS, AgentAction.OPEN_PAGE, AgentAction.EXTRACT_TEXT, AgentAction.VERIFY_JOB, AgentAction.FINISH], 5, 0, (0, 0), (1, 1, 1), True),
        ("invalid-json", "Malformed planner JSON uses policy fallback", [AgentAction.SEARCH_JOBS, AgentAction.OPEN_PAGE, AgentAction.EXTRACT_TEXT, AgentAction.VERIFY_JOB, AgentAction.FINISH], 5, 0, (0, 0), (1, 1, 1), True),
        ("verifier-recovery", "Verifier rejection recovers with visual evidence", [AgentAction.SEARCH_JOBS, AgentAction.OPEN_PAGE, AgentAction.EXTRACT_TEXT, AgentAction.VERIFY_JOB, AgentAction.EXTRACT_VISUAL, AgentAction.VERIFY_JOB, AgentAction.FINISH], 6, 1, (1, 1), (0, 0, 0), True),
        ("all-candidates-fail", "All candidates fail and terminate cleanly", [AgentAction.SEARCH_JOBS, AgentAction.OPEN_PAGE, AgentAction.OPEN_PAGE, AgentAction.FINISH], 2, 2, (2, 0), (0, 0, 0), False),
        ("budget-exhausted", "Step budget is exhausted", [AgentAction.SEARCH_JOBS, AgentAction.OPEN_PAGE, AgentAction.EXTRACT_TEXT, AgentAction.FINISH], 2, 2, (1, 0), (0, 0, 0), False),
        ("target-reached", "Target count stops the Agent early", [AgentAction.SEARCH_JOBS, AgentAction.OPEN_PAGE, AgentAction.EXTRACT_TEXT, AgentAction.VERIFY_JOB, AgentAction.FINISH], 5, 0, (0, 0), (0, 0, 0), True),
    ]
    cases: list[AgentBenchmarkCase] = []
    for index, spec in enumerate(specs, start=1):
        case_id, scenario, actions, success, failed, recoveries, planner, completed = spec
        job = _job(index, title="AI Agent Intern" if index == 2 else None) if completed else None
        truth = (
            AgentFieldGroundTruth(
                title=f"AI Agent Intern {index}",
                company=f"Fixture Labs {index}",
                location="Remote",
            )
            if completed
            else None
        )
        cases.append(
            AgentBenchmarkCase(
                case_id=case_id,
                scenario=scenario,
                state=_benchmark_state(
                    actions=actions,
                    status="completed" if completed else "stopped",
                    reason=(
                        "target_reached"
                        if completed
                        else "budget_exhausted"
                        if case_id == "budget-exhausted"
                        else "no_action_available"
                    ),
                    successful_calls=success,
                    failed_calls=failed,
                    recoveries=recoveries,
                    planner=planner,
                    job=job,
                ),
                ground_truth=truth,
            )
        )
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic hybrid Agent evidence")
    parser.add_argument("--output-dir", default="docs/results")
    args = parser.parse_args()
    result = evaluate_agent_cases(build_deterministic_benchmark_cases())
    paths = write_agent_benchmark_artifacts(result, args.output_dir)
    print(paths["json"])
    print(paths["markdown"])


if __name__ == "__main__":
    main()
