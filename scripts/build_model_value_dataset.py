"""Create the frozen controlled C3 model-value dataset at a new root."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from web_task_agent.model_value_dataset import (  # noqa: E402
    CURRENT_DATASET_ID,
    build_model_value_dataset,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evaluations" / CURRENT_DATASET_ID,
        help="New dataset root; existing paths are rejected.",
    )
    parser.add_argument("--dataset-id", default=CURRENT_DATASET_ID)
    parser.add_argument("--dataset-version", default="3.0.0")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        destination = build_model_value_dataset(
            args.output,
            dataset_id=args.dataset_id,
            dataset_version=args.dataset_version,
        )
    except FileExistsError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        json.dumps(
            {
                "dataset_root": str(destination),
                "dataset_id": args.dataset_id,
                "dataset_version": args.dataset_version,
                "case_count": 60,
                "group_count": 30,
                "split_counts": {"dev": 20, "test": 40},
                "human_review_status": "pending",
                "network_access": False,
                "model_calls": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
