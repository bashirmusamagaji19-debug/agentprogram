"""Real-site benchmark v2: provider matrix over a catalog of job URLs.

This module owns benchmark case metadata, provider result summaries,
matrix execution, and Markdown/JSON artifact rendering.  It reuses
``EvaluationRunner`` and ``WebTaskWorkflow`` for the actual extraction
— this layer is purely orchestration and reporting.
"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

from pydantic import BaseModel, Field, field_validator

from web_task_agent.evaluation import EvaluationResult, EvaluationTask

SUPPORTED_BENCHMARK_PROVIDERS = {
    "baseline",
    "llm-demo",
    "deepseek",
    "qwen",
    "qwen-vl",
}


# ── Catalog ────────────────────────────────────────────────────────────


class BenchmarkCase(BaseModel):
    """A single benchmark sample with metadata for interview analysis."""

    case_id: str
    company: str
    ats: str
    role_family: str
    keyword: str
    location: str = "Remote"
    skills: list[str] = Field(default_factory=list)
    url: str
    expected_signal: str
    notes: str = ""

    @field_validator("url")
    @classmethod
    def _url_must_be_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("url must be an https URL")
        return value

    def to_evaluation_task(self) -> EvaluationTask:
        return EvaluationTask(
            keyword=self.keyword,
            location=self.location,
            target_count=1,
            skills=self.skills,
            seed_urls=[self.url],
        )


def build_real_site_benchmark_v2_cases() -> list[BenchmarkCase]:
    """Return the current real-site benchmark catalog.

    These URLs are live external pages and **will** drift over time.
    The benchmark records failure categories so a dead or changed page
    becomes data instead of a silent test assumption.

    .. note::
        The URLs overlap with ``evaluation.build_real_site_sample_tasks()``.
        The catalog adds company/ATS/role-family/expected-signal metadata
        that the plain ``EvaluationTask`` list does not carry.  Long-term
        the evaluation helper should derive its tasks from this catalog.
    """
    return [
        BenchmarkCase(
            case_id="anthropic-claude-evangelist",
            company="Anthropic",
            ats="greenhouse",
            role_family="ai-applications",
            keyword="Applied AI Claude Evangelist",
            location="San Francisco, CA",
            skills=["AI", "demos", "customer"],
            url="https://job-boards.greenhouse.io/anthropic/jobs/5116927008",
            expected_signal="AI application and Claude product role",
        ),
        BenchmarkCase(
            case_id="anthropic-api-platform-tpm",
            company="Anthropic",
            ats="greenhouse",
            role_family="platform",
            keyword="Technical Program Manager, API Platform",
            location="San Francisco, CA",
            skills=["API", "platform", "program management"],
            url="https://job-boards.greenhouse.io/anthropic/jobs/5256303008",
            expected_signal="API platform role",
        ),
        BenchmarkCase(
            case_id="scale-ai-builder-intern",
            company="ScaleAI",
            ats="greenhouse",
            role_family="ai-internship",
            keyword="AI Builder Intern",
            location="San Francisco, CA; New York, NY",
            skills=["AI", "Python", "intern"],
            url="https://job-boards.greenhouse.io/scaleai/jobs/4703343005",
            expected_signal="AI internship role",
        ),
        BenchmarkCase(
            case_id="scale-ai-deployment-strategist",
            company="ScaleAI",
            ats="greenhouse",
            role_family="deployment",
            keyword="AI Deployment Strategist",
            location="San Francisco, CA; New York, NY",
            skills=["AI", "strategy", "deployment"],
            url="https://job-boards.greenhouse.io/scaleai/jobs/4699458005",
            expected_signal="AI deployment role",
        ),
        BenchmarkCase(
            case_id="scale-ai-strategy-consultant",
            company="ScaleAI",
            ats="greenhouse",
            role_family="strategy",
            keyword="AI Strategy Consultant",
            location="San Francisco, CA",
            skills=["AI", "consulting", "strategy"],
            url="https://job-boards.greenhouse.io/scaleai/jobs/4472223005",
            expected_signal="AI strategy role",
        ),
        BenchmarkCase(
            case_id="reddit-analytics-engineer-us",
            company="Reddit",
            ats="greenhouse",
            role_family="analytics",
            keyword="Analytics Engineer",
            location="Remote - United States",
            skills=["SQL", "Python", "analytics"],
            url="https://job-boards.greenhouse.io/reddit/jobs/7958354",
            expected_signal="analytics engineering role",
        ),
        BenchmarkCase(
            case_id="reddit-analytics-engineer-toronto",
            company="Reddit",
            ats="greenhouse",
            role_family="analytics",
            keyword="Analytics Engineer Toronto",
            location="Toronto, Canada",
            skills=["SQL", "Python", "analytics"],
            url="https://job-boards.greenhouse.io/reddit/jobs/7958385",
            expected_signal="analytics engineering role",
        ),
        BenchmarkCase(
            case_id="discord-developer-solutions",
            company="Discord",
            ats="greenhouse",
            role_family="developer-platform",
            keyword="Director Developer Solutions",
            location="San Francisco Bay Area",
            skills=["developer relations", "platform", "leadership"],
            url="https://job-boards.greenhouse.io/discord/jobs/8480100002",
            expected_signal="developer platform leadership role",
        ),
    ]


def parse_benchmark_providers(raw: str | None) -> list[str]:
    """Parse a comma-separated provider list with dedup and validation."""
    if not raw:
        return ["baseline", "llm-demo"]
    providers: list[str] = []
    for item in raw.split(","):
        provider = item.strip()
        if not provider:
            continue
        if provider not in SUPPORTED_BENCHMARK_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_BENCHMARK_PROVIDERS))
            raise ValueError(
                f"Unsupported benchmark provider: {provider!r}. "
                f"Supported: {supported}"
            )
        if provider not in providers:
            providers.append(provider)
    return providers or ["baseline", "llm-demo"]


# ── Results & rendering ────────────────────────────────────────────────


class BenchmarkProviderResult(BaseModel):
    """Aggregated result for one provider across all benchmark cases."""

    provider: str
    total_tasks: int
    completed_tasks: int
    success_rate: float
    total_valid_jobs: int
    average_pages_visited: float
    failure_counts: dict[str, int] = Field(default_factory=dict)
    elapsed_seconds: float
    report_path: str = ""

    @classmethod
    def from_evaluation(
        cls,
        *,
        provider: str,
        result: EvaluationResult,
        elapsed_seconds: float,
    ) -> "BenchmarkProviderResult":
        return cls(
            provider=provider,
            total_tasks=result.total_tasks,
            completed_tasks=result.completed_tasks,
            success_rate=result.success_rate,
            total_valid_jobs=result.total_valid_jobs,
            average_pages_visited=result.average_pages_visited,
            failure_counts=result.failure_counts,
            elapsed_seconds=round(elapsed_seconds, 2),
            report_path=(
                result.report_path.as_posix() if result.report_path else ""
            ),
        )


class BenchmarkMatrixResult(BaseModel):
    """Full benchmark matrix: cases × providers."""

    cases: list[BenchmarkCase]
    providers: list[BenchmarkProviderResult]

    @property
    def best_provider(self) -> str:
        if not self.providers:
            return ""
        best = max(
            self.providers,
            key=lambda p: (p.success_rate, p.total_valid_jobs),
        )
        return best.provider


def _failure_summary(failure_counts: dict[str, int]) -> str:
    if not failure_counts:
        return "-"
    return ", ".join(
        f"{key}={value}" for key, value in sorted(failure_counts.items())
    )


def render_benchmark_markdown(result: BenchmarkMatrixResult) -> str:
    lines = [
        "# Real Site Benchmark V2",
        "",
        "## Summary",
        "",
        f"- Cases: {len(result.cases)}",
        f"- Providers: {', '.join(p.provider for p in result.providers)}",
        f"- Best provider: {result.best_provider or '-'}",
        "",
        "## Provider Matrix",
        "",
        "| Provider | Completed | Success Rate | Valid Jobs | Failure Counts |",
        "|---|---:|---:|---:|---|",
    ]
    for provider in result.providers:
        lines.append(
            f"| {provider.provider} "
            f"| {provider.completed_tasks}/{provider.total_tasks} "
            f"| {provider.success_rate:.2f} "
            f"| {provider.total_valid_jobs} "
            f"| {_failure_summary(provider.failure_counts)} |"
        )

    lines.extend(
        [
            "",
            "## Case Catalog",
            "",
            "| Case ID | Company | ATS | Role Family | Keyword | Location | URL | Expected Signal |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for case in result.cases:
        lines.append(
            f"| {case.case_id} | {case.company} | {case.ats} "
            f"| {case.role_family} | {case.keyword} | {case.location} "
            f"| {case.url} | {case.expected_signal} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_benchmark_artifacts(
    *,
    result: BenchmarkMatrixResult,
    output_dir: str | Path,
) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "benchmark-v2.json"
    md_path = output / "benchmark-v2.md"
    json_path.write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )
    md_path.write_text(render_benchmark_markdown(result), encoding="utf-8")
    return json_path, md_path


# ── Matrix runner ──────────────────────────────────────────────────────


async def run_benchmark_matrix(
    *,
    cases: list[BenchmarkCase],
    providers: list[str],
    output_dir: str | Path,
    args,
    run_provider=None,
) -> BenchmarkMatrixResult:
    """Run every provider against the same case list.

    *run_provider* is an injectable async callable
    ``(provider, tasks, output_dir, args) -> BenchmarkProviderResult``.
    When ``None``, the caller **must** supply it — this module deliberately
    does not import CLI builders to avoid circular dependencies.
    """
    if run_provider is None:
        raise RuntimeError(
            "run_provider must be injected by the CLI layer "
            "(run_cli_benchmark_v2 provides it)"
        )
    tasks = [case.to_evaluation_task() for case in cases]
    provider_results: list[BenchmarkProviderResult] = []
    for provider in providers:
        provider_output = Path(output_dir) / provider
        result = await run_provider(provider, tasks, provider_output, args)
        provider_results.append(result)
    return BenchmarkMatrixResult(cases=cases, providers=provider_results)
