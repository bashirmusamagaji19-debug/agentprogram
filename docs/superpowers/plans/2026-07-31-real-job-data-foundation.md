# Real Job Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an append-only, evidence-backed pipeline that discovers and evaluates real Chinese AI internship postings from public official recruitment pages.

**Architecture:** Add a focused `real_jobs` package beside the existing workflow. It owns source catalogs, immutable snapshots, normalized job identities, failure records, ground truth, and evaluation; existing browser, extractor, verifier, matcher, and visual-provider interfaces remain the execution dependencies. Raw run artifacts stay ignored, while the source catalog, ground truth, schemas, and final reports are versioned.

**Tech Stack:** Python 3.11+, Pydantic 2, SQLite, JSON/JSONL, existing browser-use/LangGraph providers, pytest, Ruff

---

## File Map

- Create `src/web_task_agent/real_jobs/models.py`: source, snapshot, normalized job, failure, annotation, and feedback contracts.
- Create `src/web_task_agent/real_jobs/catalog.py`: load and validate official recruitment sources.
- Create `src/web_task_agent/real_jobs/artifacts.py`: append-only JSONL artifact persistence.
- Create `src/web_task_agent/real_jobs/identity.py`: URL normalization, content hashes, and cross-day deduplication.
- Create `src/web_task_agent/real_jobs/discovery.py`: discover real detail URLs from official listing pages with bounded rendering fallback.
- Create `src/web_task_agent/real_jobs/runner.py`: bounded static -> rendered page capture.
- Create `src/web_task_agent/real_jobs/processor.py`: text extraction, verification, role classification, evidence mapping, and optional visual recovery.
- Create `src/web_task_agent/real_jobs/evaluation.py`: ground-truth scoring and acceptance gates.
- Create `src/web_task_agent/real_jobs/cli.py`: real-run and frozen-snapshot evaluation entrypoints.
- Create `src/web_task_agent/real_jobs/__init__.py`: stable public imports.
- Create `data/real-jobs/source-catalog.json`: versioned, manually verified official sources.
- Create `data/real-jobs/ground-truth.jsonl`: versioned human annotations, initially empty.
- Create `tests/real_jobs/`: focused deterministic tests.
- Modify `src/web_task_agent/cli.py`: delegate new flags to the focused CLI module.
- Modify `.gitignore`: ignore raw snapshots and daily run directories, keep final reports and annotations.
- Modify `README.md`: document evidence boundaries and commands after verification.

### Task 1: Restore a Reproducible Development Entry Point

**Files:**
- Modify: `README.md`
- Test: `tests/test_agent_release_check.py`

- [ ] **Step 1: Recreate the editable installation without deleting the existing environment**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -c "import web_task_agent; print(web_task_agent.__version__)"
```

Expected: editable installation succeeds and prints `0.1.0`.

- [ ] **Step 2: Verify the installed console entry point**

Run:

```powershell
.\.venv\Scripts\web-task-agent.exe --version
.\.venv\Scripts\web-task-agent.exe --release-check
```

Expected: version prints `0.1.0`; all six release-check stages pass.

- [ ] **Step 3: Add the clean-environment bootstrap command to README**

Add this exact block under local setup:

```markdown
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\web-task-agent.exe --release-check
```

- [ ] **Step 4: Run the release-check contract test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_release_check.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the environment baseline**

```powershell
git add README.md
git commit -m "docs: make local bootstrap reproducible"
```

### Task 2: Define Real-Job Domain Contracts

**Files:**
- Create: `src/web_task_agent/real_jobs/__init__.py`
- Create: `src/web_task_agent/real_jobs/models.py`
- Create: `tests/real_jobs/test_models.py`

- [ ] **Step 1: Write model validation tests**

Create `tests/real_jobs/test_models.py`:

```python
from datetime import UTC, datetime

import pytest

from web_task_agent.real_jobs.models import (
    CaptureMethod,
    FailureCode,
    JobClass,
    PageSnapshot,
    SourceEntry,
)


def test_source_entry_accepts_public_official_source() -> None:
    source = SourceEntry(
        source_id="example-careers",
        company="Example",
        entry_url="https://careers.example.com/jobs",
        source_type="official",
        cities=["北京", "上海"],
    )
    assert source.source_id == "example-careers"
    assert source.cities == ["北京", "上海"]


def test_source_entry_rejects_non_https_url() -> None:
    with pytest.raises(ValueError, match="https"):
        SourceEntry(
            source_id="bad",
            company="Bad",
            entry_url="http://example.com/jobs",
            source_type="official",
        )


def test_snapshot_requires_failure_code_when_capture_fails() -> None:
    with pytest.raises(ValueError, match="failure_code"):
        PageSnapshot(
            snapshot_id="snap-1",
            run_id="run-1",
            source_id="example-careers",
            url="https://careers.example.com/jobs/1",
            captured_at=datetime.now(UTC),
            method=CaptureMethod.STATIC_HTML,
            success=False,
        )


def test_metric_enums_have_stable_serialized_values() -> None:
    assert FailureCode.ACCESS_RESTRICTED.value == "access_restricted"
    assert JobClass.ADJACENT.value == "adjacent"
```

- [ ] **Step 2: Run the tests and confirm the package is missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_models.py -q
```

Expected: collection fails with `ModuleNotFoundError: web_task_agent.real_jobs`.

- [ ] **Step 3: Implement the complete model contract**

Create `src/web_task_agent/real_jobs/models.py`:

```python
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class SourceType(StrEnum):
    OFFICIAL = "official"
    CAMPUS = "campus"
    PUBLIC_ATS = "public_ats"


class CaptureMethod(StrEnum):
    STATIC_HTML = "static_html"
    RENDERED_BROWSER = "rendered_browser"
    VISUAL_FALLBACK = "visual_fallback"


class FailureCode(StrEnum):
    SOURCE_UNREACHABLE = "source_unreachable"
    ACCESS_RESTRICTED = "access_restricted"
    JOB_REMOVED = "job_removed"
    PAGE_CHANGED = "page_changed"
    RENDER_REQUIRED = "render_required"
    EXTRACTION_INCOMPLETE = "extraction_incomplete"
    VERIFICATION_REJECTED = "verification_rejected"
    DUPLICATE_JOB = "duplicate_job"
    MODEL_INVALID_OUTPUT = "model_invalid_output"


class JobClass(StrEnum):
    CORE = "core"
    ADJACENT = "adjacent"
    IRRELEVANT = "irrelevant"


class JobStatus(StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"
    UNKNOWN = "unknown"


class FeedbackDecision(StrEnum):
    PREPARE_TO_APPLY = "prepare_to_apply"
    NOT_CONSIDERING = "not_considering"
    FALSE_RECOMMENDATION = "false_recommendation"
    APPLIED = "applied"
    EXPIRED = "expired"


class SourceEntry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source_id: str = Field(min_length=1)
    company: str = Field(min_length=1)
    entry_url: HttpUrl
    source_type: SourceType
    cities: list[str] = Field(default_factory=list)
    role_hints: list[str] = Field(default_factory=list)
    enabled: bool = True
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    notes: str = ""

    @model_validator(mode="after")
    def require_https(self) -> SourceEntry:
        if self.entry_url.scheme != "https":
            raise ValueError("entry_url must use https")
        return self


class PageSnapshot(BaseModel):
    snapshot_id: str
    run_id: str
    source_id: str
    url: HttpUrl
    canonical_url: HttpUrl | None = None
    captured_at: datetime
    method: CaptureMethod
    success: bool
    http_status: int | None = None
    title: str = ""
    content: str = ""
    content_sha256: str = ""
    evidence_excerpt: str = ""
    failure_code: FailureCode | None = None
    error: str = ""
    retry_count: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def require_failure_code(self) -> PageSnapshot:
        if not self.success and self.failure_code is None:
            raise ValueError("failure_code is required for failed capture")
        return self


class RealJobRecord(BaseModel):
    job_id: str
    snapshot_id: str
    source_id: str
    url: HttpUrl
    company: str
    title: str
    location: str
    job_class: JobClass
    requirements: str = ""
    responsibilities: str = ""
    first_seen_at: datetime
    last_confirmed_at: datetime
    status: JobStatus = JobStatus.ACTIVE
    extraction_method: CaptureMethod
    field_evidence: dict[str, str] = Field(default_factory=dict)


class GroundTruthRecord(BaseModel):
    annotation_id: str
    snapshot_id: str
    annotated_at: datetime
    is_valid_job: bool
    company: str
    title: str
    location: str
    job_class: JobClass
    duplicate_of: str | None = None
    field_evidence: dict[str, str] = Field(default_factory=dict)


class UserFeedback(BaseModel):
    job_id: str
    recorded_at: datetime
    decision: FeedbackDecision
    reason: str = ""
```

Create `src/web_task_agent/real_jobs/__init__.py`:

```python
from web_task_agent.real_jobs.models import (
    CaptureMethod,
    FeedbackDecision,
    FailureCode,
    GroundTruthRecord,
    JobClass,
    JobStatus,
    PageSnapshot,
    RealJobRecord,
    SourceEntry,
    SourceType,
    UserFeedback,
)

__all__ = [
    "CaptureMethod",
    "FeedbackDecision",
    "FailureCode",
    "GroundTruthRecord",
    "JobClass",
    "JobStatus",
    "PageSnapshot",
    "RealJobRecord",
    "SourceEntry",
    "SourceType",
    "UserFeedback",
]
```

- [ ] **Step 4: Run model tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_models.py -q
```

Expected: `4 passed`.

- [ ] **Step 5: Commit domain contracts**

```powershell
git add src/web_task_agent/real_jobs tests/real_jobs/test_models.py
git commit -m "feat: define real job evidence contracts"
```

### Task 3: Add the Versioned Official Source Catalog

**Files:**
- Create: `src/web_task_agent/real_jobs/catalog.py`
- Create: `data/real-jobs/source-catalog.json`
- Create: `tests/real_jobs/test_catalog.py`

- [ ] **Step 1: Write catalog contract tests**

Create `tests/real_jobs/test_catalog.py`:

```python
import json

import pytest

from web_task_agent.real_jobs.catalog import load_source_catalog


def test_catalog_loads_unique_enabled_sources(tmp_path) -> None:
    path = tmp_path / "sources.json"
    path.write_text(json.dumps([{
        "source_id": "example",
        "company": "Example",
        "entry_url": "https://careers.example.com/jobs",
        "source_type": "official",
    }]), encoding="utf-8")
    assert [source.source_id for source in load_source_catalog(path)] == ["example"]


def test_catalog_rejects_duplicate_source_ids(tmp_path) -> None:
    path = tmp_path / "sources.json"
    item = {
        "source_id": "duplicate",
        "company": "Example",
        "entry_url": "https://careers.example.com/jobs",
        "source_type": "official",
    }
    path.write_text(json.dumps([item, item]), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate source_id"):
        load_source_catalog(path)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_catalog.py -q
```

Expected: import fails because `catalog.py` does not exist.

- [ ] **Step 3: Implement strict catalog loading**

Create `src/web_task_agent/real_jobs/catalog.py`:

```python
import json
from pathlib import Path

from web_task_agent.real_jobs.models import SourceEntry


def load_source_catalog(path: str | Path) -> list[SourceEntry]:
    catalog_path = Path(path)
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("source catalog must be a JSON array")
    sources = [SourceEntry.model_validate(item) for item in raw]
    ids = [source.source_id for source in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate source_id in source catalog")
    return [source for source in sources if source.enabled]
```

- [ ] **Step 4: Create the empty-but-valid production catalog**

Create `data/real-jobs/source-catalog.json` with:

```json
[]
```

Do not add a company until its official page has been opened manually, confirmed to be public HTTPS, and recorded by the operational plan.

- [ ] **Step 5: Run catalog tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_catalog.py -q
```

Expected: `2 passed`.

- [ ] **Step 6: Commit catalog support**

```powershell
git add src/web_task_agent/real_jobs/catalog.py data/real-jobs/source-catalog.json tests/real_jobs/test_catalog.py
git commit -m "feat: add validated official source catalog"
```

### Task 4: Persist Immutable JSONL Evidence

**Files:**
- Create: `src/web_task_agent/real_jobs/artifacts.py`
- Create: `tests/real_jobs/test_artifacts.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write append and corruption tests**

Create `tests/real_jobs/test_artifacts.py`:

```python
from datetime import UTC, datetime

import pytest

from web_task_agent.real_jobs.artifacts import JsonlStore
from web_task_agent.real_jobs.models import CaptureMethod, PageSnapshot


def snapshot(snapshot_id: str) -> PageSnapshot:
    return PageSnapshot(
        snapshot_id=snapshot_id,
        run_id="run-1",
        source_id="example",
        url=f"https://careers.example.com/{snapshot_id}",
        captured_at=datetime.now(UTC),
        method=CaptureMethod.STATIC_HTML,
        success=True,
        content="real job content",
    )


def test_store_appends_without_overwriting(tmp_path) -> None:
    store = JsonlStore(tmp_path / "snapshots.jsonl", PageSnapshot)
    store.append(snapshot("one"))
    store.append(snapshot("two"))
    assert [item.snapshot_id for item in store.read_all()] == ["one", "two"]


def test_store_reports_corrupt_line_number(tmp_path) -> None:
    path = tmp_path / "snapshots.jsonl"
    path.write_text("{}\nnot-json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1|line 2"):
        JsonlStore(path, PageSnapshot).read_all()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_artifacts.py -q
```

Expected: import fails because `artifacts.py` does not exist.

- [ ] **Step 3: Implement typed JSONL storage**

Create `src/web_task_agent/real_jobs/artifacts.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


class JsonlStore(Generic[ModelT]):
    def __init__(self, path: str | Path, model_type: type[ModelT]) -> None:
        self.path = Path(path)
        self.model_type = model_type

    def append(self, item: ModelT) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(item.model_dump_json())
            handle.write("\n")

    def read_all(self) -> list[ModelT]:
        if not self.path.exists():
            return []
        items: list[ModelT] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            try:
                items.append(self.model_type.model_validate(json.loads(line)))
            except Exception as exc:
                raise ValueError(
                    f"invalid JSONL record at {self.path}:{line_number}"
                ) from exc
        return items
```

- [ ] **Step 4: Ignore raw operational data**

Append to `.gitignore`:

```gitignore
data/real-jobs/runs/
data/real-jobs/snapshots/
data/real-jobs/user-feedback.local.jsonl
```

Do not ignore `data/real-jobs/source-catalog.json`, `data/real-jobs/ground-truth.jsonl`, or final redacted evaluation reports.

- [ ] **Step 5: Run artifact tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_artifacts.py -q
```

Expected: `2 passed`.

- [ ] **Step 6: Commit append-only persistence**

```powershell
git add .gitignore src/web_task_agent/real_jobs/artifacts.py tests/real_jobs/test_artifacts.py
git commit -m "feat: persist append-only real job evidence"
```

### Task 5: Normalize Identity and Deduplicate Across Days

**Files:**
- Create: `src/web_task_agent/real_jobs/identity.py`
- Create: `tests/real_jobs/test_identity.py`

- [ ] **Step 1: Write deterministic identity tests**

Create `tests/real_jobs/test_identity.py`:

```python
from web_task_agent.real_jobs.identity import content_sha256, job_identity, normalize_url


def test_normalize_url_removes_tracking_and_fragment() -> None:
    value = normalize_url(
        "https://careers.example.com/jobs/1?utm_source=test&id=1#apply"
    )
    assert value == "https://careers.example.com/jobs/1?id=1"


def test_job_identity_ignores_case_and_whitespace() -> None:
    first = job_identity("Example", "AI  Intern", "北京", "same body")
    second = job_identity(" example ", "ai intern", " 北京 ", "same body")
    assert first == second


def test_content_hash_is_stable() -> None:
    assert content_sha256("a  b\n") == content_sha256("a b")
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_identity.py -q
```

Expected: import fails because `identity.py` does not exist.

- [ ] **Step 3: Implement normalization and hashes**

Create `src/web_task_agent/real_jobs/identity.py`:

```python
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_KEYS = {"utm_campaign", "utm_medium", "utm_source", "spm"}


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if key.casefold() not in TRACKING_KEYS
        )
    )
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path, query, ""))


def content_sha256(content: str) -> str:
    normalized = normalize_text(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def job_identity(company: str, title: str, location: str, content: str) -> str:
    payload = "|".join(
        normalize_text(value) for value in (company, title, location, content)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run identity tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_identity.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit identity logic**

```powershell
git add src/web_task_agent/real_jobs/identity.py tests/real_jobs/test_identity.py
git commit -m "feat: add cross-day job identity"
```

### Task 6: Discover Detail URLs From Official Listing Pages

**Files:**
- Create: `src/web_task_agent/real_jobs/discovery.py`
- Create: `tests/real_jobs/test_discovery.py`

- [ ] **Step 1: Write static, rendered-fallback, and empty-result tests**

Create `tests/real_jobs/test_discovery.py`:

```python
import pytest

from web_task_agent.models import BrowserPage
from web_task_agent.real_jobs.discovery import OfficialSourceDiscoverer
from web_task_agent.real_jobs.models import CaptureMethod, FailureCode, SourceEntry


def source() -> SourceEntry:
    return SourceEntry(
        source_id="example",
        company="Example",
        entry_url="https://careers.example.com/jobs",
        source_type="official",
    )


@pytest.mark.asyncio
async def test_discovers_real_detail_links_from_static_listing() -> None:
    async def static(url: str) -> BrowserPage:
        return BrowserPage(
            url=url,
            content='<a href="/jobs/123">AI Intern</a><a href="/about">About</a>',
        )

    result = await OfficialSourceDiscoverer(static, static).discover(source())
    assert result.urls == ["https://careers.example.com/jobs/123"]
    assert result.method is CaptureMethod.STATIC_HTML


@pytest.mark.asyncio
async def test_uses_rendered_links_when_static_listing_has_none() -> None:
    async def static(url: str) -> BrowserPage:
        return BrowserPage(url=url, content="JavaScript required")

    async def rendered(url: str) -> BrowserPage:
        return BrowserPage(
            url=url,
            content="Rendered jobs",
            metadata={"links": ["https://careers.example.com/jobs/456"]},
        )

    result = await OfficialSourceDiscoverer(static, rendered).discover(source())
    assert result.urls == ["https://careers.example.com/jobs/456"]
    assert result.method is CaptureMethod.RENDERED_BROWSER


@pytest.mark.asyncio
async def test_reports_page_changed_when_no_detail_links_exist() -> None:
    async def empty(url: str) -> BrowserPage:
        return BrowserPage(url=url, content="No job links")

    result = await OfficialSourceDiscoverer(empty, empty).discover(source())
    assert result.urls == []
    assert result.failure_code is FailureCode.PAGE_CHANGED
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_discovery.py -q
```

Expected: import fails because `discovery.py` does not exist.

- [ ] **Step 3: Implement generic official-page discovery**

Create `src/web_task_agent/real_jobs/discovery.py`:

```python
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from web_task_agent.models import BrowserPage
from web_task_agent.real_jobs.models import CaptureMethod, FailureCode, SourceEntry
from web_task_agent.search_discovery import discover_job_links

Fetch = Callable[[str], Awaitable[BrowserPage]]


@dataclass(frozen=True)
class SourceDiscoveryResult:
    source_id: str
    urls: list[str]
    method: CaptureMethod
    failure_code: FailureCode | None = None
    error: str = ""


class OfficialSourceDiscoverer:
    def __init__(self, static_fetch: Fetch, rendered_fetch: Fetch) -> None:
        self.static_fetch = static_fetch
        self.rendered_fetch = rendered_fetch

    async def discover(self, source: SourceEntry) -> SourceDiscoveryResult:
        errors: list[str] = []
        stages = (
            (CaptureMethod.STATIC_HTML, self.static_fetch),
            (CaptureMethod.RENDERED_BROWSER, self.rendered_fetch),
        )
        for method, fetch in stages:
            try:
                page = await fetch(str(source.entry_url))
                raw_links = page.metadata.get("links", [])
                if not isinstance(raw_links, list):
                    raw_links = []
                urls = discover_job_links(
                    page.content,
                    base_url=page.url,
                    raw_links=[str(item) for item in raw_links],
                )
                if urls:
                    return SourceDiscoveryResult(source.source_id, urls, method)
                errors.append(f"{method.value}: no detail links")
            except Exception as exc:
                errors.append(f"{method.value}: {type(exc).__name__}: {exc}")
        return SourceDiscoveryResult(
            source_id=source.source_id,
            urls=[],
            method=CaptureMethod.RENDERED_BROWSER,
            failure_code=FailureCode.PAGE_CHANGED,
            error=" | ".join(errors),
        )
```

- [ ] **Step 4: Run discovery tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_discovery.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit source discovery**

```powershell
git add src/web_task_agent/real_jobs/discovery.py tests/real_jobs/test_discovery.py
git commit -m "feat: discover official job detail URLs"
```

### Task 7: Implement Bounded Capture Fallback

**Files:**
- Create: `src/web_task_agent/real_jobs/runner.py`
- Create: `tests/real_jobs/test_runner.py`

- [ ] **Step 1: Write fallback-order and failure tests**

Create `tests/real_jobs/test_runner.py` with fake async callables that assert these exact outcomes:

```python
import pytest

from web_task_agent.models import BrowserPage
from web_task_agent.real_jobs.models import CaptureMethod, FailureCode, SourceEntry
from web_task_agent.real_jobs.runner import CaptureRunner


@pytest.mark.asyncio
async def test_static_success_does_not_call_fallbacks() -> None:
    calls: list[str] = []

    async def static(url: str) -> BrowserPage:
        calls.append("static")
        return BrowserPage(url=url, title="AI Intern", content="Company Example Responsibilities Python")

    async def forbidden(url: str) -> BrowserPage:
        raise AssertionError("fallback must not run")

    runner = CaptureRunner(static_fetch=static, rendered_fetch=forbidden)
    result = await runner.capture(
        SourceEntry(
            source_id="example",
            company="Example",
            entry_url="https://careers.example.com/jobs",
            source_type="official",
        ),
        "https://careers.example.com/jobs/1",
        "run-1",
    )
    assert result.success is True
    assert result.method is CaptureMethod.STATIC_HTML
    assert calls == ["static"]


@pytest.mark.asyncio
async def test_empty_static_page_recovers_with_rendered_browser() -> None:
    async def static(url: str) -> BrowserPage:
        return BrowserPage(url=url, content="")

    async def rendered(url: str) -> BrowserPage:
        return BrowserPage(url=url, title="AI Intern", content="real rendered job body")

    runner = CaptureRunner(static_fetch=static, rendered_fetch=rendered)
    result = await runner.capture(_source(), "https://careers.example.com/jobs/1", "run-1")
    assert result.success is True
    assert result.method is CaptureMethod.RENDERED_BROWSER


@pytest.mark.asyncio
async def test_all_capture_methods_fail_with_explicit_code() -> None:
    async def failing(url: str) -> BrowserPage:
        raise TimeoutError("timeout")

    runner = CaptureRunner(static_fetch=failing, rendered_fetch=failing)
    result = await runner.capture(_source(), "https://careers.example.com/jobs/1", "run-1")
    assert result.success is False
    assert result.failure_code is FailureCode.SOURCE_UNREACHABLE


@pytest.mark.asyncio
async def test_access_restriction_is_not_reported_as_network_failure() -> None:
    from web_task_agent.browser import PageHttpError

    async def restricted(url: str) -> BrowserPage:
        raise PageHttpError("forbidden", status_code=403)

    runner = CaptureRunner(static_fetch=restricted, rendered_fetch=restricted)
    result = await runner.capture(_source(), "https://careers.example.com/jobs/1", "run-1")
    assert result.failure_code is FailureCode.ACCESS_RESTRICTED


def _source() -> SourceEntry:
    return SourceEntry(
        source_id="example",
        company="Example",
        entry_url="https://careers.example.com/jobs",
        source_type="official",
    )
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_runner.py -q
```

Expected: import fails because `runner.py` does not exist.

- [ ] **Step 3: Implement the minimal capture runner**

Create `src/web_task_agent/real_jobs/runner.py` with `CaptureRunner` accepting injected async `static_fetch` and `rendered_fetch`. Before the stage loop, add:

```python
def classify_capture_failure(exc: Exception) -> FailureCode:
    if isinstance(exc, PageHttpError) and exc.status_code in {401, 403, 429}:
        return FailureCode.ACCESS_RESTRICTED
    if isinstance(exc, PageHttpError) and exc.status_code in {404, 410}:
        return FailureCode.JOB_REMOVED
    if isinstance(exc, PageEmptyError):
        return FailureCode.RENDER_REQUIRED
    if isinstance(exc, (TimeoutError, PageTimeoutError, OSError)):
        return FailureCode.SOURCE_UNREACHABLE
    return FailureCode.PAGE_CHANGED
```

Its `capture(source, url, run_id)` must initialize `errors: list[str] = []` and `failure_codes: list[FailureCode] = []`, then execute:

```python
for method, fetch in self._stages:
    started = perf_counter()
    try:
        page = await fetch(url)
        if page.content.strip():
            return PageSnapshot(
                snapshot_id=self._snapshot_id(run_id, source.source_id, url, method),
                run_id=run_id,
                source_id=source.source_id,
                url=url,
                canonical_url=normalize_url(url),
                captured_at=datetime.now(UTC),
                method=method,
                success=True,
                title=page.title,
                content=page.content,
                content_sha256=content_sha256(page.content),
                evidence_excerpt=page.content[:500],
                latency_ms=(perf_counter() - started) * 1000,
            )
        errors.append(f"{method.value}: empty content")
    except Exception as exc:
        errors.append(f"{method.value}: {type(exc).__name__}: {exc}")
        failure_codes.append(classify_capture_failure(exc))
final_failure = next(
    (
        code
        for code in failure_codes
        if code in {FailureCode.ACCESS_RESTRICTED, FailureCode.JOB_REMOVED}
    ),
    failure_codes[-1] if failure_codes else FailureCode.RENDER_REQUIRED,
)
return PageSnapshot(
    snapshot_id=self._snapshot_id(run_id, source.source_id, url, self._stages[-1][0]),
    run_id=run_id,
    source_id=source.source_id,
    url=url,
    captured_at=datetime.now(UTC),
    method=self._stages[-1][0],
    success=False,
    failure_code=final_failure,
    error=" | ".join(errors),
    retry_count=max(0, len(self._stages) - 1),
)
```

Implement `_snapshot_id` as SHA-256 over `run_id|source_id|normalize_url(url)|method.value`. Do not catch cancellation exceptions; re-raise `asyncio.CancelledError`.

- [ ] **Step 4: Run runner tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_runner.py -q
```

Expected: `4 passed`.

- [ ] **Step 5: Commit bounded capture**

```powershell
git add src/web_task_agent/real_jobs/runner.py tests/real_jobs/test_runner.py
git commit -m "feat: add bounded real page capture"
```

If any stage reports `ACCESS_RESTRICTED` or `JOB_REMOVED`, preserve that code instead of replacing it with a later generic failure.

### Task 8: Convert Snapshots Into Evidence-Backed Jobs

**Files:**
- Create: `src/web_task_agent/real_jobs/processor.py`
- Create: `tests/real_jobs/test_processor.py`

- [ ] **Step 1: Write text-success and visual-recovery tests**

Create `tests/real_jobs/test_processor.py` with a labeled `PageSnapshot`, `PageExtractor`, and `JobVerifier(required_keywords=["AI"])`. Assert that a valid text record does not call the fake visual extractor, that a low-confidence text result calls it once, and that an empty visual result returns `FailureCode.MODEL_INVALID_OUTPUT` instead of a job.

Use this visual fake for the recovery case:

```python
class FakeVisualExtractor:
    calls = 0

    async def extract(self, page: BrowserPage) -> VisualExtractionResult:
        self.calls += 1
        return VisualExtractionResult(
            url=page.url,
            success=True,
            fields=VisualJobFields(
                title="大模型应用实习生",
                company="Example",
                location="北京",
                requirements="Python, RAG",
                responsibilities="构建 AI 应用",
                confidence=0.9,
            ),
        )
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_processor.py -q
```

Expected: import fails because `processor.py` does not exist.

- [ ] **Step 3: Implement snapshot processing**

Create `src/web_task_agent/real_jobs/processor.py`. Define `ProcessingResult` as a frozen dataclass with `job: RealJobRecord | None`, `failure_code: FailureCode | None`, and `reasons: list[str]`. Implement `RealJobProcessor(extractor, verifier, visual_extractor=None)` with this flow:

```python
async def process(self, snapshot: PageSnapshot, source: SourceEntry) -> ProcessingResult:
    if not snapshot.success:
        return ProcessingResult(None, snapshot.failure_code, [snapshot.error])
    page = BrowserPage(
        url=str(snapshot.url),
        title=snapshot.title,
        content=snapshot.content,
        source=snapshot.method.value,
    )
    job = self.extractor.extract(page)
    verification = self.verifier.verify(job)
    method = snapshot.method
    if not verification.is_valid and self.visual_extractor is not None:
        visual = await self.visual_extractor.extract(page)
        if visual.success and visual.fields is not None:
            job = job_from_visual_fields(page=page, fields=visual.fields)
            verification = self.verifier.verify(job)
            method = CaptureMethod.VISUAL_FALLBACK
        else:
            return ProcessingResult(
                None,
                FailureCode.MODEL_INVALID_OUTPUT,
                [visual.error or "visual result has no supported fields"],
            )
    if not verification.is_valid:
        return ProcessingResult(
            None,
            FailureCode.VERIFICATION_REJECTED,
            verification.reasons,
        )
    job_class = classify_job(job)
    record = RealJobRecord(
        job_id=job_identity(job.company, job.title, job.location, snapshot.content),
        snapshot_id=snapshot.snapshot_id,
        source_id=source.source_id,
        url=job.url,
        company=job.company,
        title=job.title,
        location=job.location,
        job_class=job_class,
        requirements=job.requirements,
        responsibilities=job.responsibilities,
        first_seen_at=snapshot.captured_at,
        last_confirmed_at=snapshot.captured_at,
        extraction_method=method,
        field_evidence=evidence_for(job, snapshot.content),
    )
    return ProcessingResult(record, None, [])
```

Implement `classify_job(job)` with versioned keyword sets: core contains `agent`, `大模型应用`, `ai 应用`; adjacent contains `rag`, `算法`, `模型服务`, `ai 平台`, `大模型后端`; otherwise irrelevant. Implement `evidence_for` so a field is included only when its normalized exact value occurs in normalized snapshot content. Do not synthesize evidence for visual-only values.

- [ ] **Step 4: Run processor tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_processor.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit snapshot processing**

```powershell
git add src/web_task_agent/real_jobs/processor.py tests/real_jobs/test_processor.py
git commit -m "feat: process real snapshots with evidence"
```

### Task 9: Add Ground-Truth Evaluation and Acceptance Gates

**Files:**
- Create: `src/web_task_agent/real_jobs/evaluation.py`
- Create: `data/real-jobs/ground-truth.jsonl`
- Create: `tests/real_jobs/test_evaluation.py`

- [ ] **Step 1: Write metric-separation tests**

Create `tests/real_jobs/test_evaluation.py` using two predictions and two annotations. Assert exact separate values for valid-job precision, company accuracy, title accuracy, location accuracy, class accuracy, evidence support, and dedup F1; also assert that a report with fewer than 100 annotations cannot pass final acceptance.

Use this expected summary:

```python
assert result.sample_count == 2
assert result.valid_job_precision == 1.0
assert result.company_accuracy == 1.0
assert result.title_accuracy == 0.5
assert result.location_accuracy == 1.0
assert result.job_class_accuracy == 0.5
assert result.passed_final_gate is False
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_evaluation.py -q
```

Expected: import fails because the evaluator does not exist.

- [ ] **Step 3: Implement explicit evaluation models and functions**

Create `RealJobEvaluationResult(BaseModel)` with these fields:

```python
dataset_version: str
evaluated_at: datetime
sample_count: int
valid_job_precision: float
company_accuracy: float
title_accuracy: float
location_accuracy: float
job_class_accuracy: float
evidence_support_rate: float
dedup_precision: float
dedup_recall: float
dedup_f1: float
top20_relevance: float | None
passed_final_gate: bool
gate_failures: list[str]
```

Implement `evaluate_real_jobs(predictions, annotations, dataset_version)` by joining on `snapshot_id`, normalizing exact text with `normalize_text`, computing every denominator independently, and setting `passed_final_gate` only when:

```python
sample_count >= 100
valid_job_precision >= 0.95
company_accuracy >= 0.98
title_accuracy >= 0.98
location_accuracy >= 0.90
job_class_accuracy >= 0.90
dedup_f1 >= 0.90
```

If a denominator is zero, return `0.0` and add a named gate failure. Never reuse task completion or loop termination as extraction accuracy.

- [ ] **Step 4: Create the versioned annotation file**

Create `data/real-jobs/ground-truth.jsonl` as a zero-byte UTF-8 file. An empty file is valid during development but cannot pass final acceptance.

- [ ] **Step 5: Run evaluation tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_evaluation.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit evaluation contracts**

```powershell
git add src/web_task_agent/real_jobs/evaluation.py data/real-jobs/ground-truth.jsonl tests/real_jobs/test_evaluation.py
git commit -m "feat: evaluate real job evidence"
```

### Task 10: Wire a Narrow Real-Job CLI

**Files:**
- Create: `src/web_task_agent/real_jobs/cli.py`
- Create: `tests/real_jobs/test_cli.py`
- Modify: `src/web_task_agent/cli.py`

- [ ] **Step 1: Write parser and nonzero-failure tests**

Add tests asserting:

```python
assert main(["--real-job-run", "--real-job-catalog", str(catalog), "--real-job-output-dir", str(output)]) == 0
assert (output / "run-summary.json").exists()
assert main(["--real-job-evaluate", "--real-job-ground-truth", str(missing)]) == 2
```

The first test must inject fake source discovery and page fetchers; no test may access the network.

- [ ] **Step 2: Run tests and verify parser rejection**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_cli.py -q
```

Expected: failure because the new arguments are unknown.

- [ ] **Step 3: Add CLI flags**

Add these exact parser arguments in `build_parser()`:

```python
parser.add_argument("--real-job-run", action="store_true")
parser.add_argument("--real-job-evaluate", action="store_true")
parser.add_argument("--real-job-catalog", default="data/real-jobs/source-catalog.json")
parser.add_argument("--real-job-ground-truth", default="data/real-jobs/ground-truth.jsonl")
parser.add_argument("--real-job-output-dir", default="data/real-jobs/runs/latest")
parser.add_argument("--real-job-max-sources", type=int, default=20)
```

Delegate execution immediately after release/portfolio command handling:

```python
if args.real_job_run or args.real_job_evaluate:
    from web_task_agent.real_jobs.cli import run_real_job_command

    return await run_real_job_command(args)
```

- [ ] **Step 4: Implement command orchestration**

`run_real_job_command(args)` must validate paths before network setup, write `run-summary.json` only from actual artifacts, return `2` for invalid catalog/annotations, and close browser and visual providers in `finally`. It must not print success if zero sources were executed or if all sources failed.

- [ ] **Step 5: Run CLI tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\real_jobs\test_cli.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit CLI wiring**

```powershell
git add src/web_task_agent/cli.py src/web_task_agent/real_jobs/cli.py tests/real_jobs/test_cli.py
git commit -m "feat: expose real job evidence commands"
```

### Task 11: Verify the Foundation and Document Its Boundaries

**Files:**
- Modify: `README.md`
- Create: `docs/work-log/2026-07-31-real-job-data-foundation.md`

- [ ] **Step 1: Run focused quality checks**

```powershell
.\.venv\Scripts\python.exe -m ruff check src\web_task_agent\real_jobs tests\real_jobs
.\.venv\Scripts\python.exe -m pytest tests\real_jobs -q
.\.venv\Scripts\python.exe -m pytest --cov=web_task_agent --cov-report=term
```

Expected: Ruff passes, real-job tests pass, full suite passes, coverage remains at least 70%.

- [ ] **Step 2: Run the existing release gate**

```powershell
.\.venv\Scripts\web-task-agent.exe --release-check
git diff --check
```

Expected: all six release stages pass and `git diff --check` is silent.

- [ ] **Step 3: Document only verified commands and boundaries**

Add a README section that states:

```markdown
真实岗位模式只接受公开官方招聘来源。原始快照与每日运行目录不提交到 Git；来源目录、人工标注和脱敏最终报告版本化。fixture、历史真实 URL 评测与本轮 14 天真实数据集分别报告，不合并指标。
```

Document the run and evaluation commands, but do not publish acceptance metrics until 100 annotations and 14 daily snapshots exist.

- [ ] **Step 4: Record the implementation evidence**

The work log must include commit IDs, exact test outputs, current source count, current annotation count, known failures, and the next operational command. Do not copy historical metrics as current results.

- [ ] **Step 5: Commit the verified foundation**

```powershell
git add README.md docs/work-log/2026-07-31-real-job-data-foundation.md
git commit -m "docs: explain real job evidence workflow"
```
