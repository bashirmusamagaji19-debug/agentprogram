"""Paired comparison of the existing official pipeline and Search + Fetch."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field

from web_task_agent.browser import BrowserUseClient, HttpPageLoader
from web_task_agent.deepseek_search import DeepSeekApiError, DeepSeekWebClient, FetchFallbackLoader
from web_task_agent.extractor import PageExtractor
from web_task_agent.job_sources import AggregatorPageLoader, DiscoveredJob, JobSource
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import BrowserPage, UserProfile
from web_task_agent.official_api import OfficialApiContentFetcher
from web_task_agent.official_list_source import OfficialListSource
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.search_conditions import job_condition_reasons
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow


class ComparisonCase(BaseModel):
    keyword: str = Field(min_length=1, max_length=90)
    location: str = Field(min_length=1, max_length=20)
    target_count: int = Field(default=5, ge=1, le=50)
    official_specs: list[str] = Field(default_factory=lambda: ["tencent-campus", "xiaomi"])
    skills: list[str] = Field(default_factory=lambda: ["Python", "RAG", "Agent"])


def merge_candidates(official: list[DiscoveredJob], search: list[DiscoveredJob], *, limit: int) -> list[DiscoveredJob]:
    # Keep richer official metadata for shared URLs, then round-robin the sources.
    by_url = {job.url.rstrip("/"): job for job in official}
    merged: list[DiscoveredJob] = []
    seen: set[str] = set()
    for index in range(max(len(official), len(search))):
        for batch in (official, search):
            if index >= len(batch):
                continue
            job = batch[index]
            key = job.url.rstrip("/")
            if key not in seen and len(merged) < limit:
                seen.add(key)
                merged.append(by_url.get(key, job))
    return merged


def _queries(case: ComparisonCase, limit: int) -> list[str]:
    query = f"{case.keyword} {case.location} 招聘"
    if "实习" not in query and "intern" not in query.casefold():
        query += " 实习"
    return [f"{query}{suffix}"[:128] for suffix in ("", " site:jobs.feishu.cn", " site:mokahr.com")][:limit]


async def _evaluate_arm(
    *, name: str, case: ComparisonCase, candidates: list[DiscoveredJob], directory: Path,
    client: DeepSeekWebClient | None, max_fetch_calls: int,
    page_loader: Callable[[str], Awaitable[BrowserPage]] | None,
) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=False)
    repository = JobRepository(directory / "agent.db")
    repository.initialize()
    primary = AggregatorPageLoader(
        candidates, official_api=OfficialApiContentFetcher(), http_loader=page_loader or HttpPageLoader(15),
        repository=repository,
    )
    loader = FetchFallbackLoader(primary, client, max_calls=max_fetch_calls) if client else primary
    snapshots: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    async def record(url: str) -> BrowserPage:
        try:
            page = await loader(url)
        except Exception as error:
            errors.append({"url": url, "error_type": type(error).__name__})
            raise
        snapshots.append(page.model_dump(mode="json"))
        return page

    user = UserProfile(
        keyword=case.keyword, location=case.location, target_count=case.target_count,
        skills=case.skills, seed_urls=[job.url for job in candidates],
    )
    verifier = JobVerifier()
    state = await WebTaskWorkflow(
        browser=BrowserUseClient(page_loader=record, discovery_only=True),
        extractor=PageExtractor(), matcher=JobMatcher(), verifier=verifier,
        repository=repository, reporter=MarkdownReporter(directory / "reports"), enforce_search_conditions=True,
    ).run_with_langgraph(user=user, run_id=name)
    # The workflow caps recommendations at target_count. Count all verified pool
    # entries separately so both arms reaching the target cannot hide extra yield.
    valid, _ = verifier.dedupe([
        job for job in state.metadata.get("extracted_jobs", [])
        if verifier.verify(job).is_valid and not job_condition_reasons(case.keyword, case.location, job)
    ])
    result = {
        "candidate_count": len(candidates), "pages_read": len(snapshots), "valid_count": len(valid),
        "candidate_yield": round(len(valid) / len(candidates), 4) if candidates else 0.0,
        "company_count": len({job.company.casefold() for job in valid}),
        "jobs": [job.model_dump(mode="json") for job in valid],
        "recommendations": [job.model_dump(mode="json") for job in state.jobs],
        "target_reached": len(state.jobs) >= case.target_count,
        "failed_pages": errors, "filtered_jobs": state.metadata.get("filtered_jobs", []),
        "resolution_log": primary.resolution_log,
        "fetch_diagnostics": loader.diagnostics if client else [],
        "api_usage": client.usage if client else {
            "search": {"attempts": 0, "billable": 0}, "fetch": {"attempts": 0, "billable": 0},
        },
    }
    result["estimated_api_cost_usd"] = round(sum(row["billable"] for row in result["api_usage"].values()) * 0.00075, 6)
    (directory / "pages.json").write_text(json.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _delta(baseline: dict[str, Any], augmented: dict[str, Any]) -> dict[str, Any]:
    old_urls = {job["url"] for job in baseline["jobs"]}
    new_urls = {job["url"] for job in augmented["jobs"]}
    old_companies = {job["company"].casefold() for job in baseline["jobs"]}
    new_companies = {job["company"].casefold() for job in augmented["jobs"]}
    gain = augmented["valid_count"] - baseline["valid_count"]
    cost = augmented["estimated_api_cost_usd"] - baseline["estimated_api_cost_usd"]
    return {
        "valid_count": gain,
        "new_valid_urls": sorted(new_urls - old_urls), "lost_valid_urls": sorted(old_urls - new_urls),
        "new_company_count": len(new_companies - old_companies),
        "elapsed_seconds": round(augmented["elapsed_seconds"] - baseline["elapsed_seconds"], 3),
        "estimated_api_cost_usd": round(cost, 6),
        "cost_per_net_additional_valid_job_usd": round(cost / gain, 6) if gain > 0 else None,
    }


def _write_report(report: dict[str, Any], output: Path) -> None:
    (output / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Existing pipeline vs. DeepSeek Search + Fetch", "", report["scope"], "",
        "Pipeline-valid jobs are not independently labelled ground truth or proof of an open vacancy.",
        "No recall/accuracy claim is made. Each arm has a cold private repository; official candidates are shared.",
        "Elapsed time includes shared official discovery time in both arms; arm order alternates by case.",
        "API cost is an estimate at $0.75 per 1,000 successful non-empty calls; excludes other network/compute costs.",
        "", "| Case | Arm | Candidates | Valid | Companies | Seconds | API USD |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for index, case in enumerate(report["cases"], 1):
        for name, arm in case["arms"].items():
            lines.append(
                f"| {index} | {name} | {arm['candidate_count']} | {arm['valid_count']} | "
                f"{arm['company_count']} | {arm['elapsed_seconds']:.2f} | {arm['estimated_api_cost_usd']:.6f} |"
            )
        lines.extend(["", f"Case {index}: {case['case']['keyword']} / {case['case']['location']}",
                      f"Net valid gain: {case['delta']['valid_count']}; "
                      f"new URLs: {len(case['delta']['new_valid_urls'])}; "
                      f"lost URLs: {len(case['delta']['lost_valid_urls'])}.", ""])
    (output / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run_search_comparison(
    *, cases: list[ComparisonCase], output_dir: str | Path,
    client_factory: Callable[[], DeepSeekWebClient], candidate_budget: int = 20,
    max_search_calls: int = 3, max_fetch_calls: int = 10,
    source_factory: Callable[[ComparisonCase], JobSource] | None = None,
    page_loader: Callable[[str], Awaitable[BrowserPage]] | None = None,
) -> dict[str, Any]:
    if not cases or not 1 <= candidate_budget <= 100 or not 1 <= max_search_calls <= 3 or max_fetch_calls < 0:
        raise ValueError("require cases, candidate budget 1..100, search calls 1..3, and nonnegative Fetch budget")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(), "status": "running",
        "scope": "Exactly two arms; rule extraction/matching; identical explicit official sources and conditions.",
        "candidate_budget": candidate_budget, "max_search_calls_per_case": max_search_calls,
        "max_fetch_calls_per_case": max_fetch_calls,
        "selection_policy": "official-first round-robin with search, exact-URL dedupe, same total candidate cap",
        "cases": [],
    }
    _write_report(report, output)
    for index, case in enumerate(cases):
        case_dir = output / f"case-{index + 1}"
        case_dir.mkdir()
        print(f"Case {index + 1}: {case.keyword} / {case.location}: official discovery", flush=True)
        started = perf_counter()
        source = source_factory(case) if source_factory else OfficialListSource(
            specs=case.official_specs, keyword=case.keyword, location=case.location,
        )
        official = (await source.discover(candidate_budget))[:candidate_budget]
        discovery_seconds = perf_counter() - started
        (case_dir / "official-candidates.json").write_text(
            json.dumps([asdict(job) for job in official], ensure_ascii=False, indent=2), encoding="utf-8",
        )
        paired: dict[str, Any] = {
            "case": case.model_dump(mode="json"), "official_discovery_seconds": round(discovery_seconds, 3),
            "source_diagnostics": getattr(source, "diagnostics", []), "arms": {},
        }
        order = ("baseline", "search_fetch") if index % 2 == 0 else ("search_fetch", "baseline")
        for name in order:
            print(f"  {name}: starting", flush=True)
            started = perf_counter()
            client = client_factory() if name == "search_fetch" else None
            search_jobs: list[DiscoveredJob] = []
            search_diagnostics: list[dict[str, Any]] = []
            fatal_api_error = False
            if client:
                for query in _queries(case, max_search_calls):
                    try:
                        jobs = client.search(query, count=min(50, candidate_budget))
                    except DeepSeekApiError as error:
                        search_diagnostics.append({"query": query, "error_type": type(error).__name__,
                                                   "api_status": error.api_status})
                        if error.api_status in (401, 402, 403, 429):
                            fatal_api_error = True
                            break
                    else:
                        search_jobs.extend(jobs)
                        search_diagnostics.append({"query": query, "count": len(jobs)})
            candidates = merge_candidates(official, search_jobs, limit=candidate_budget)
            arm = await _evaluate_arm(
                name=name, case=case, candidates=candidates, directory=case_dir / name,
                client=client, max_fetch_calls=0 if fatal_api_error else max_fetch_calls, page_loader=page_loader,
            )
            arm.update(
                elapsed_seconds=round(discovery_seconds + perf_counter() - started, 3),
                search_diagnostics=search_diagnostics,
                candidates=[asdict(job) for job in candidates],
            )
            paired["arms"][name] = arm
            print(f"  {name}: {arm['valid_count']} valid / {len(candidates)} candidates", flush=True)
        paired["arms"] = {name: paired["arms"][name] for name in ("baseline", "search_fetch")}
        paired["delta"] = _delta(*paired["arms"].values())
        report["cases"].append(paired)
        _write_report(report, output)
    report["status"] = "completed"
    _write_report(report, output)
    return report
