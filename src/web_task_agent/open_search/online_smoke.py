from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from .artifacts import ArtifactWriter
from .pipeline import OpenSearchPipeline
from .query_parser import DemoQueryParser
from .search_provider import SearchProviderConfigurationError, TavilySearchProvider

DEFAULT_QUERIES = (
    "找北京 Agent 开发实习，要求 Python 和 LangGraph，1 个岗位",
    "Find remote AI application engineering internships requiring Python, top 1 jobs",
    "找上海大模型应用实习，排除销售岗位，1 个岗位",
)


def _render_markdown(report: dict) -> str:
    lines = [
        "# 真实在线验收报告",
        "",
        f"- 模式：`{report['mode']}`",
        f"- 搜索 Provider：`{report['provider']}`",
        f"- 查询数：{report['query_count']}",
        f"- 可信岗位数：{report['total_verified_jobs']}",
        f"- 失败分类：`{json.dumps(report['failure_counts'], ensure_ascii=False)}`",
        "",
        "> 本报告来自配置搜索 API key 后的在线运行；岗位为零也是有效结果，",
        "> 但必须结合失败分类判断是无匹配、搜索失败还是页面验证失败。",
        "",
        "## 查询明细",
        "",
    ]
    for index, run in enumerate(report["runs"], start=1):
        lines.extend(
            [
                f"### {index}. {run['query']}",
                "",
                f"- 终止原因：`{run['terminal_reason']}`",
                f"- 候选数：{run['candidates_seen']}",
                f"- 可信岗位数：{run['verified_jobs']}",
                f"- 失败分类：`{json.dumps(run['failure_counts'], ensure_ascii=False)}`",
                f"- Artifact：`{run['artifact_dir']}`",
                "",
            ]
        )
    return "\n".join(lines)


async def run_online_smoke(
    queries: Sequence[str],
    *,
    output_dir: Path,
    provider,
    source_verifier=None,
) -> dict:
    if not queries:
        raise ValueError("at least one query is required")

    output_dir = Path(output_dir)
    parser = DemoQueryParser()
    aggregate_failures: Counter[str] = Counter()
    runs: list[dict] = []

    for index, query in enumerate(queries, start=1):
        intent = parser.parse(query)
        relative_artifact_dir = Path("runs") / f"query-{index:02d}"
        result = await OpenSearchPipeline(
            provider,
            source_verifier=source_verifier,
            verify_reachability=True,
        ).run(
            intent,
            output_dir=output_dir / relative_artifact_dir,
            limit=intent.target_count,
        )
        failure_counts = Counter(failure.code for failure in result.failures)
        aggregate_failures.update(failure_counts)
        runs.append(
            {
                "query": query,
                "run_id": result.summary.run_id,
                "terminal_reason": result.summary.terminal_reason,
                "candidates_seen": result.summary.candidates_seen,
                "verified_jobs": result.summary.verified_count,
                "failure_counts": dict(sorted(failure_counts.items())),
                "provider_quality": result.summary.metadata,
                "artifact_dir": relative_artifact_dir.as_posix(),
            }
        )

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "online",
        "provider": type(provider).__name__,
        "query_count": len(runs),
        "total_verified_jobs": sum(run["verified_jobs"] for run in runs),
        "failure_counts": dict(sorted(aggregate_failures.items())),
        "runs": runs,
    }
    writer = ArtifactWriter(output_dir)
    writer.write_json("online-smoke-report.json", report)
    (output_dir / "online-smoke-report.md").write_text(
        _render_markdown(report), encoding="utf-8"
    )
    return report


def _default_output_dir() -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("outputs") / "open-search-online-smoke" / timestamp


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run auditable online job-search smoke queries through Tavily."
    )
    parser.add_argument("--query", action="append", dest="queries")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)

    try:
        provider = TavilySearchProvider.from_environment()
    except SearchProviderConfigurationError as exc:
        print(f"Online smoke cannot start: {exc}", file=sys.stderr)
        return 2

    output_dir = args.output_dir or _default_output_dir()
    report = asyncio.run(
        run_online_smoke(
            args.queries or DEFAULT_QUERIES,
            output_dir=output_dir,
            provider=provider,
        )
    )
    print(f"Online smoke report: {output_dir / 'online-smoke-report.md'}")
    print(f"Verified jobs: {report['total_verified_jobs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
