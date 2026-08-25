from __future__ import annotations

import os
import platform
import time
from collections import Counter, defaultdict, deque
from pathlib import Path
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .models import SearchCandidate
from .pipeline import OpenSearchPipeline
from .query_parser import DemoQueryParser
from .search_provider import (
    FixtureSearchProvider,
    SearchProviderConfigurationError,
    TavilySearchProvider,
)

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
ARTIFACT_ROOT = Path(os.getenv("OPEN_SEARCH_ARTIFACT_DIR", "outputs/open-search-runs"))
app = FastAPI(title="Open Web Job Search Agent")
_runs: dict[str, dict] = {}


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


_MAX_RUNS = _positive_env_int("OPEN_SEARCH_MAX_RUNS", 100)
_MAX_REQUESTS_PER_MINUTE = _positive_env_int("OPEN_SEARCH_RATE_LIMIT_PER_MINUTE", 20)
_request_windows: dict[str, deque[float]] = defaultdict(deque)


def _cors_origins() -> list[str]:
    raw = os.getenv("OPEN_SEARCH_CORS_ORIGINS", "").strip()
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class RunRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    mode: str = Field(default="demo", pattern="^(demo|online)$")


def _fixture_provider() -> FixtureSearchProvider:
    return FixtureSearchProvider(
        [
            SearchCandidate(
                url="https://job-boards.greenhouse.io/example/jobs/123",
                title="Agent Intern",
                snippet="Python LangGraph Beijing",
                source="Example AI",
            ),
            SearchCandidate(
                url="https://jobs.example.com/careers/ai-engineer",
                title="AI Application Intern",
                snippet="Python FastAPI remote",
                source="Example Labs",
            ),
        ]
    )


async def _execute(run_id: str, request: RunRequest) -> None:
    record = _runs[run_id]
    record["status"] = "running"
    try:
        parser = DemoQueryParser()
        intent = parser.parse(request.query)
        provider = (
            _fixture_provider()
            if request.mode == "demo"
            else TavilySearchProvider.from_environment()
        )
        result = await OpenSearchPipeline(
            provider, verify_reachability=request.mode == "online"
        ).run(
            intent,
            output_dir=ARTIFACT_ROOT / run_id,
            limit=intent.target_count,
        )
        record.update(status="completed", summary=result.summary.model_dump(mode="json"))
    except SearchProviderConfigurationError as exc:
        record.update(status="failed", error={"code": "search_api_error", "message": str(exc)})
    except Exception as exc:
        record.update(status="failed", error={"code": "internal_error", "message": str(exc)})


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/version")
async def version() -> dict[str, object]:
    return {
        "project": "web-task-agent",
        "version": "0.1.0",
        "python": platform.python_version(),
        "limits": {
            "max_runs": _MAX_RUNS,
            "rate_limit_per_minute": _MAX_REQUESTS_PER_MINUTE,
        },
    }


@app.get("/api/capabilities")
async def capabilities() -> dict[str, object]:
    return {
        "modes": {
            "demo": {"available": True, "requires_api_key": False},
            "online": {
                "available": bool(os.getenv("TAVILY_API_KEY", "").strip()),
                "requires_api_key": True,
                "provider": "tavily",
            },
        },
        "artifacts": ["jobs", "trace"],
    }


@app.post("/api/runs", status_code=202)
async def create_run(
    request: RunRequest, background_tasks: BackgroundTasks, http_request: Request
) -> dict:
    client_key = http_request.client.host if http_request.client else "unknown"
    now = time.monotonic()
    window = _request_windows[client_key]
    while window and now - window[0] >= 60:
        window.popleft()
    if len(window) >= _MAX_REQUESTS_PER_MINUTE:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "message": "Too many runs from this client; retry later.",
            },
            headers={"Retry-After": "60"},
        )
    window.append(now)
    if len(_runs) >= _MAX_RUNS:
        oldest_id = next(iter(_runs))
        _runs.pop(oldest_id, None)
    run_id = uuid4().hex
    intent = DemoQueryParser().parse(request.query)
    _runs[run_id] = {
        "run_id": run_id,
        "status": "queued",
        "intent": intent.model_dump(mode="json"),
    }
    if request.mode == "online" and not os.getenv("TAVILY_API_KEY", "").strip():
        raise HTTPException(
            status_code=400,
            detail={"code": "search_api_error", "message": "TAVILY_API_KEY is required"},
        )
    background_tasks.add_task(_execute, run_id, request)
    return _runs[run_id]


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail={"code": "run_not_found"})
    return _runs[run_id]


def _artifact_lines(run_id: str, name: str) -> list[dict]:
    path = ARTIFACT_ROOT / run_id / name
    if not path.exists():
        return []
    return [
        __import__("json").loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _artifact_json(run_id: str, name: str) -> dict | None:
    path = ARTIFACT_ROOT / run_id / name
    if not path.exists():
        return None
    return __import__("json").loads(path.read_text(encoding="utf-8"))


def _require_run(run_id: str) -> None:
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail={"code": "run_not_found"})


@app.get("/api/runs/{run_id}/jobs")
async def get_jobs(run_id: str) -> dict:
    _require_run(run_id)
    return {"run_id": run_id, "jobs": _artifact_lines(run_id, "jobs.jsonl")}


@app.get("/api/runs/{run_id}/trace")
async def get_trace(run_id: str) -> dict:
    _require_run(run_id)
    return {"run_id": run_id, "trace": _artifact_lines(run_id, "execution-trace.jsonl")}


@app.get("/api/runs/{run_id}/evaluation")
async def get_evaluation(run_id: str) -> dict:
    _require_run(run_id)
    summary = _artifact_json(run_id, "run-summary.json")
    if summary is not None:
        failures = _artifact_lines(run_id, "failures.jsonl")
        failure_counts = dict(Counter(item.get("code", "unknown") for item in failures))
        return {
            "run_id": run_id,
            "evaluation": {
                "available": True,
                "summary": summary,
                "jobs_count": len(_artifact_lines(run_id, "jobs.jsonl")),
                "trace_count": len(_artifact_lines(run_id, "execution-trace.jsonl")),
                "failure_counts": failure_counts,
            },
        }
    return {
        "run_id": run_id,
        "evaluation": {
            "available": False,
            "message": "Run is still in progress; evaluation is not available yet.",
        },
    }
