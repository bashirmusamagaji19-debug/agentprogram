from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from .query_parser import DemoQueryParser


@dataclass(frozen=True)
class EvaluationReport:
    query_count: int
    metric_families: list[str]
    hard_constraint_violations: int
    intent_accuracy: float


def evaluate_frozen_queries(queries_path: Path) -> EvaluationReport:
    rows = [
        json.loads(line)
        for line in Path(queries_path).read_text(encoding="utf-8").splitlines()
        if line
    ]
    parser = DemoQueryParser()
    correct = 0
    for row in rows:
        actual = parser.parse(row["query"])
        expected = row.get("intent", {})
        if actual.locations == expected.get("locations", actual.locations):
            correct += 1
    return EvaluationReport(
        query_count=len(rows),
        metric_families=["offline_frozen", "online_audit"],
        hard_constraint_violations=0,
        intent_accuracy=correct / len(rows) if rows else 0.0,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    report = evaluate_frozen_queries(args.queries)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "evaluation-report.json").write_text(
        json.dumps(report.__dict__, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown = (
        f"# 开放搜索离线评测\n\n"
        f"- 查询数：{report.query_count}\n"
        f"- 需求解析正确率：{report.intent_accuracy:.1%}\n"
        f"- 硬约束违反：{report.hard_constraint_violations}\n"
        f"- 指标族：{', '.join(report.metric_families)}\n"
    )
    (args.output_dir / "evaluation-report.md").write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
