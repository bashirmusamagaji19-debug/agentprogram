"""CLI entrypoint for the versioned real-job benchmark."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

main = importlib.import_module("web_task_agent.real_job_benchmark").main

if __name__ == "__main__":
    raise SystemExit(main())
