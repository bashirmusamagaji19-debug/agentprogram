from __future__ import annotations

from pydantic import BaseModel, Field

from web_task_agent.agent_models import DecisionAgentState
from web_task_agent.agent_planner import PlannerTelemetry

SUPPORTED_PLANNER_BENCHMARK_PROVIDERS = ("deterministic", "deepseek", "qwen")


class PlannerBenchmarkCaseResult(BaseModel):
    case_id: str
    scenario: str
    terminal_status: str
    terminal_reason: str
    completed: bool
    terminated: bool
    consumed_steps: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    successful_tool_calls: int = Field(ge=0)
    recovery_attempts: int = Field(ge=0)
    successful_recoveries: int = Field(ge=0)
    planner_calls: int = Field(ge=0)
    fallback_decisions: int = Field(ge=0)
    invalid_actions: int = Field(ge=0)
    tool_latency_ms: float = Field(ge=0)
    runtime_latency_ms: float = Field(ge=0)
    error: str = ""

    @classmethod
    def from_state(
        cls,
        *,
        case_id: str,
        scenario: str,
        state: DecisionAgentState,
        runtime_latency_ms: float,
    ) -> PlannerBenchmarkCaseResult:
        metrics = state.metrics
        return cls(
            case_id=case_id,
            scenario=scenario,
            terminal_status=state.terminal_status,
            terminal_reason=state.terminal_reason,
            completed=(
                state.terminal_status == "completed"
                and state.terminal_reason == "target_reached"
            ),
            terminated=state.terminal_status != "running",
            consumed_steps=state.budget.consumed_steps,
            tool_calls=metrics.tool_calls,
            successful_tool_calls=metrics.successful_tool_calls,
            recovery_attempts=metrics.recovery_attempts,
            successful_recoveries=metrics.successful_recoveries,
            planner_calls=metrics.planner_calls,
            fallback_decisions=metrics.fallback_decisions,
            invalid_actions=metrics.invalid_actions,
            tool_latency_ms=metrics.total_latency_ms,
            runtime_latency_ms=runtime_latency_ms,
        )


class PlannerBenchmarkProviderResult(BaseModel):
    provider: str
    model: str
    status: str = "executed"
    error: str = ""
    total_cases: int = Field(ge=0)
    completed_cases: int = Field(ge=0)
    task_completion_rate: float = Field(ge=0, le=1)
    terminated_cases: int = Field(ge=0)
    loop_termination_rate: float = Field(ge=0, le=1)
    tool_success_rate: float = Field(ge=0, le=1)
    recovery_success_rate: float = Field(ge=0, le=1)
    planner_calls: int = Field(ge=0)
    invalid_actions: int = Field(ge=0)
    invalid_action_rate: float = Field(ge=0, le=1)
    fallback_decisions: int = Field(ge=0)
    fallback_rate: float = Field(ge=0, le=1)
    average_steps: float = Field(ge=0)
    max_steps: int = Field(ge=0)
    runtime_latency_ms: float = Field(ge=0)
    planner_latency_ms: float = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cases: list[PlannerBenchmarkCaseResult] = Field(default_factory=list)


def parse_planner_benchmark_providers(raw: str | None) -> list[str]:
    requested = raw.split(",") if raw else list(SUPPORTED_PLANNER_BENCHMARK_PROVIDERS)
    providers = list(
        dict.fromkeys(item.strip().lower() for item in requested if item.strip())
    )
    for provider in providers:
        if provider not in SUPPORTED_PLANNER_BENCHMARK_PROVIDERS:
            raise ValueError(f"Unsupported planner benchmark provider: {provider!r}")
    return providers


def summarize_planner_provider(
    *,
    provider: str,
    model: str,
    case_results: list[PlannerBenchmarkCaseResult],
    telemetry: PlannerTelemetry | None = None,
) -> PlannerBenchmarkProviderResult:
    telemetry = telemetry or PlannerTelemetry()
    total_cases = len(case_results)
    completed_cases = sum(case.completed for case in case_results)
    terminated_cases = sum(case.terminated for case in case_results)
    tool_calls = sum(case.tool_calls for case in case_results)
    successful_tool_calls = sum(case.successful_tool_calls for case in case_results)
    recovery_attempts = sum(case.recovery_attempts for case in case_results)
    successful_recoveries = sum(case.successful_recoveries for case in case_results)
    planner_calls = sum(case.planner_calls for case in case_results)
    invalid_actions = sum(case.invalid_actions for case in case_results)
    fallback_decisions = sum(case.fallback_decisions for case in case_results)
    steps = [case.consumed_steps for case in case_results]

    return PlannerBenchmarkProviderResult(
        provider=provider,
        model=model,
        total_cases=total_cases,
        completed_cases=completed_cases,
        task_completion_rate=completed_cases / total_cases if total_cases else 0.0,
        terminated_cases=terminated_cases,
        loop_termination_rate=terminated_cases / total_cases if total_cases else 0.0,
        tool_success_rate=successful_tool_calls / tool_calls if tool_calls else 0.0,
        recovery_success_rate=(
            successful_recoveries / recovery_attempts if recovery_attempts else 0.0
        ),
        planner_calls=planner_calls,
        invalid_actions=invalid_actions,
        invalid_action_rate=invalid_actions / planner_calls if planner_calls else 0.0,
        fallback_decisions=fallback_decisions,
        fallback_rate=fallback_decisions / planner_calls if planner_calls else 0.0,
        average_steps=sum(steps) / total_cases if total_cases else 0.0,
        max_steps=max(steps, default=0),
        runtime_latency_ms=sum(case.runtime_latency_ms for case in case_results),
        planner_latency_ms=telemetry.total_latency_ms,
        prompt_tokens=telemetry.prompt_tokens,
        completion_tokens=telemetry.completion_tokens,
        total_tokens=telemetry.total_tokens,
        cases=case_results,
    )
