from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from pydantic import BaseModel, Field

from web_task_agent.agent_cli import build_hybrid_runtime
from web_task_agent.agent_models import DecisionAgentState
from web_task_agent.agent_planner import (
    OpenAiCompatibleAgentPlanner,
    PlannerTelemetry,
    build_configured_agent_planner,
)
from web_task_agent.browser import FakeBrowserClient
from web_task_agent.extractor import PageExtractor
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import BrowserPage, UserProfile
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow

SUPPORTED_PLANNER_BENCHMARK_PROVIDERS = ("deterministic", "deepseek", "qwen")
PLANNER_BENCHMARK_VERSION = "hybrid-agent-planner-controlled-v2"
PLANNER_BENCHMARK_SCOPE = "controlled replayable runtime scenarios"

PlannerFactory = Callable[[str], OpenAiCompatibleAgentPlanner]


class PlannerBenchmarkScenario(BaseModel):
    case_id: str
    scenario: str
    keyword: str = "AI agent intern"
    location: str = "Remote"
    target_count: int = Field(default=1, ge=1)
    seed_urls: list[str] = Field(default_factory=list)
    pages: list[BrowserPage] = Field(default_factory=list)
    broken_urls: set[str] = Field(default_factory=set)
    max_steps: int = Field(default=8, ge=1)


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
    action_sequence: list[str] = Field(default_factory=list)
    decision_sources: list[str] = Field(default_factory=list)
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
            action_sequence=[decision.action.value for decision in state.decision_history],
            decision_sources=[decision.source.value for decision in state.decision_history],
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


class PlannerBenchmarkMatrix(BaseModel):
    benchmark_version: str = PLANNER_BENCHMARK_VERSION
    benchmark_date: str = "2026-07-29"
    scope: str = PLANNER_BENCHMARK_SCOPE
    providers: list[PlannerBenchmarkProviderResult] = Field(default_factory=list)


class _ScenarioBrowser:
    def __init__(self, scenario: PlannerBenchmarkScenario) -> None:
        self._browser = FakeBrowserClient(scenario.pages)
        self._broken_urls = set(scenario.broken_urls)

    async def search(self, query: str, target_count: int) -> list[BrowserPage]:
        return await self._browser.search(query, target_count)

    async def open_url(self, url: str) -> BrowserPage:
        if url in self._broken_urls:
            raise TimeoutError(f"controlled timeout for {url}")
        return await self._browser.open_url(url)


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


def build_planner_benchmark_scenarios() -> list[PlannerBenchmarkScenario]:
    valid_url = "https://benchmark.example/jobs/ai-agent-intern"
    second_valid_url = "https://benchmark.example/jobs/ai-platform-intern"
    broken_url = "https://benchmark.example/jobs/timeout"
    rejected_url = "https://benchmark.example/jobs/operations-intern"
    valid_page = BrowserPage(
        url=valid_url,
        title="AI Agent Intern",
        content=(
            "Title: AI Agent Intern\n"
            "Company: Example AI\n"
            "Location: Remote\n"
            "Requirements: Python, LangGraph, LLM\n"
            "Responsibilities: Build and evaluate AI agents."
        ),
        source="planner-benchmark",
    )
    second_valid_page = BrowserPage(
        url=second_valid_url,
        title="AI Platform Intern",
        content=(
            "Title: AI Platform Intern\n"
            "Company: Example Platform\n"
            "Location: Remote\n"
            "Requirements: Python, FastAPI, Agent\n"
            "Responsibilities: Build reliable agent services."
        ),
        source="planner-benchmark",
    )
    rejected_page = BrowserPage(
        url=rejected_url,
        title="Operations Intern",
        content=(
            "Title: Operations Intern\n"
            "Company: Example Operations\n"
            "Location: Remote\n"
            "Requirements: Excel, scheduling\n"
            "Responsibilities: Coordinate office operations."
        ),
        source="planner-benchmark",
    )
    return [
        PlannerBenchmarkScenario(
            case_id="seed-happy-path",
            scenario="A valid seeded JD reaches the requested target.",
            seed_urls=[valid_url],
            pages=[valid_page],
        ),
        PlannerBenchmarkScenario(
            case_id="search-happy-path",
            scenario="The Agent discovers a valid JD through search.",
            pages=[valid_page],
        ),
        PlannerBenchmarkScenario(
            case_id="open-recovery",
            scenario="A timed-out URL exhausts retries before the next candidate succeeds.",
            seed_urls=[broken_url, valid_url],
            pages=[valid_page],
            broken_urls={broken_url},
            max_steps=10,
        ),
        PlannerBenchmarkScenario(
            case_id="verifier-recovery",
            scenario="A rejected JD is replaced by the next valid candidate.",
            seed_urls=[rejected_url, second_valid_url],
            pages=[rejected_page, second_valid_page],
            max_steps=10,
        ),
        PlannerBenchmarkScenario(
            case_id="budget-exhaustion",
            scenario="A one-step budget stops cleanly before target completion.",
            seed_urls=[valid_url],
            pages=[valid_page],
            max_steps=1,
        ),
    ]


async def run_planner_benchmark(
    *,
    providers: list[str],
    output_dir: str | Path,
    planner_factory: PlannerFactory | None = None,
) -> PlannerBenchmarkMatrix:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    factory = planner_factory or (
        lambda provider: build_configured_agent_planner(provider=provider)
    )
    provider_results: list[PlannerBenchmarkProviderResult] = []

    for provider in providers:
        planner: OpenAiCompatibleAgentPlanner | None = None
        if provider != "deterministic":
            try:
                planner = factory(provider)
            except Exception as exc:
                provider_results.append(_skipped_provider(provider, str(exc)))
                continue

        case_results = []
        for scenario in build_planner_benchmark_scenarios():
            case_results.append(
                await _run_scenario(
                    scenario=scenario,
                    provider=provider,
                    planner=planner,
                    output_dir=destination,
                )
            )
        provider_results.append(
            summarize_planner_provider(
                provider=provider,
                model=(
                    "deterministic-policy-v1"
                    if planner is None
                    else str(getattr(planner, "model", provider))
                ),
                case_results=case_results,
                telemetry=(getattr(planner, "telemetry", None) if planner else None),
            )
        )

    return PlannerBenchmarkMatrix(providers=provider_results)


async def _run_scenario(
    *,
    scenario: PlannerBenchmarkScenario,
    provider: str,
    planner,
    output_dir: Path,
) -> PlannerBenchmarkCaseResult:
    with TemporaryDirectory(
        prefix=f"planner-benchmark-{provider}-{scenario.case_id}-",
        dir=output_dir,
    ) as temporary_dir:
        run_dir = Path(temporary_dir)
        repository = JobRepository(run_dir / "agent.db")
        repository.initialize()
        workflow = WebTaskWorkflow(
            browser=_ScenarioBrowser(scenario),
            extractor=PageExtractor(),
            matcher=JobMatcher(),
            verifier=JobVerifier(),
            repository=repository,
            reporter=MarkdownReporter(run_dir / "reports"),
        )
        runtime = build_hybrid_runtime(workflow, planner=planner)
        user = UserProfile(
            keyword=scenario.keyword,
            location=scenario.location,
            target_count=scenario.target_count,
            skills=["Python", "LangGraph"],
            seed_urls=scenario.seed_urls,
        )
        started = perf_counter()
        state = await workflow.run_with_hybrid_agent(
            user,
            runtime=runtime,
            max_steps=scenario.max_steps,
        )
        return PlannerBenchmarkCaseResult.from_state(
            case_id=scenario.case_id,
            scenario=scenario.scenario,
            state=state,
            runtime_latency_ms=(perf_counter() - started) * 1000,
        )


def _skipped_provider(provider: str, error: str) -> PlannerBenchmarkProviderResult:
    return PlannerBenchmarkProviderResult(
        provider=provider,
        model="",
        status="skipped",
        error=error,
        total_cases=0,
        completed_cases=0,
        task_completion_rate=0,
        terminated_cases=0,
        loop_termination_rate=0,
        tool_success_rate=0,
        recovery_success_rate=0,
        planner_calls=0,
        invalid_actions=0,
        invalid_action_rate=0,
        fallback_decisions=0,
        fallback_rate=0,
        average_steps=0,
        max_steps=0,
        runtime_latency_ms=0,
        planner_latency_ms=0,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
    )


def render_planner_benchmark_markdown(matrix: PlannerBenchmarkMatrix) -> str:
    lines = [
        "# 真实 Planner 对照评测",
        "",
        f"- Benchmark version: `{matrix.benchmark_version}`",
        f"- Date: `{matrix.benchmark_date}`",
        f"- Scope: `{matrix.scope}`",
        "",
        "本评测让 deterministic、DeepSeek 和 Qwen 在同一批受控、可复现的运行时场景中决策。",
        "它衡量 Planner 决策、授权 fallback 与循环终止，不代表真实招聘网页的抽取泛化能力。",
        "",
        "## Provider 对照",
        "",
        (
            "| Provider | Model | 状态 | 任务完成率 | 循环终止率 | 工具成功率 | "
            "Fallback 率 | 非法决策率 | 平均步数 | Planner 延迟 ms | Total Token |"
        ),
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for provider in matrix.providers:
        lines.append(
            f"| {provider.provider} | {provider.model or '-'} | {provider.status} | "
            f"{provider.completed_cases}/{provider.total_cases} "
            f"({provider.task_completion_rate:.2%}) | "
            f"{provider.terminated_cases}/{provider.total_cases} "
            f"({provider.loop_termination_rate:.2%}) | "
            f"{provider.tool_success_rate:.2%} | {provider.fallback_rate:.2%} | "
            f"{provider.invalid_action_rate:.2%} | {provider.average_steps:.2f} | "
            f"{provider.planner_latency_ms:.2f} | {provider.total_tokens} |"
        )

    lines.extend(
        [
            "",
            "## 场景明细",
            "",
            (
                "| Provider | Case | 终态 | 终止原因 | 完成 | 步数 | Planner calls | "
                "Fallback | 动作序列 | 决策来源 |"
            ),
            "|---|---|---|---|---|---:|---:|---:|---|---|",
        ]
    )
    for provider in matrix.providers:
        for case in provider.cases:
            lines.append(
                f"| {provider.provider} | {case.case_id} | {case.terminal_status} | "
                f"{case.terminal_reason} | {'yes' if case.completed else 'no'} | "
                f"{case.consumed_steps} | {case.planner_calls} | "
                f"{case.fallback_decisions} | {' -> '.join(case.action_sequence)} | "
                f"{' -> '.join(case.decision_sources)} |"
            )
        if provider.error:
            lines.append(f"\n- `{provider.provider}`: {provider.error}")

    lines.extend(
        [
            "",
            "## 指标口径",
            "",
            "- 任务完成率：达到 `target_reached` 的场景比例。",
            "- 循环终止率：进入非 `running` 终态的场景比例，不能替代任务完成率。",
            "- Fallback 率与非法决策率：分母均为 Hybrid runtime 实际调用 Planner 的次数。",
            (
                "- Token：只记录 provider 返回的 prompt/completion/total 数字，"
                "不保存 prompt 或响应正文；不硬编码货币价格。"
            ),
            "",
            "## 面试表达",
            "",
            "我把 deterministic policy、DeepSeek 和 Qwen 放进同一个五场景 Hybrid Agent runtime。",
            "模型只负责正常状态下的语义选择，URL 白名单、失败恢复、重试预算和终止仍由代码控制。",
            (
                "因此我能分别报告任务完成、循环终止、非法决策、fallback、延迟与 Token，"
                "而不是只展示一次成功调用。"
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def write_planner_benchmark_artifacts(
    matrix: PlannerBenchmarkMatrix,
    output_dir: str | Path,
) -> dict[str, Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "planner-benchmark.json"
    markdown_path = destination / "planner-benchmark.md"
    json_path.write_text(
        json.dumps(matrix.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_planner_benchmark_markdown(matrix),
        encoding="utf-8",
    )
    return {"json": json_path, "markdown": markdown_path}
