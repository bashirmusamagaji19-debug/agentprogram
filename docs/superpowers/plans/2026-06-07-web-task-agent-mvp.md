# Web Task Agent MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a testable MVP that runs a LangGraph-style web-task workflow for AI internship job discovery, structured extraction, verification, SQLite persistence, and Markdown reporting.

**Architecture:** The MVP separates domain models, browser execution, extraction, verification, persistence, workflow orchestration, and reporting into small Python modules. A fake browser client is used in tests so the workflow is deterministic; the real browser-use adapter is introduced behind the same interface.

**Tech Stack:** Python 3.11+, pytest, Pydantic, SQLite, LangGraph, browser-use, Markdown reports, optional FastAPI/Streamlit in a later plan.

---

## File Structure

- `pyproject.toml`: package metadata, dependencies, pytest configuration.
- `README.md`: Chinese project overview, local setup, and MVP usage.
- `.gitignore`: Python cache, virtualenv, database, reports, browser artifacts.
- `src/web_task_agent/__init__.py`: package marker and version.
- `src/web_task_agent/models.py`: Pydantic models for user input, jobs, matches, metrics, browser pages, and workflow state.
- `src/web_task_agent/browser.py`: browser client protocol, fake browser client, and browser-use adapter boundary.
- `src/web_task_agent/extractor.py`: deterministic and LLM-ready page-to-job extraction logic.
- `src/web_task_agent/verifier.py`: completeness, relevance, confidence, and duplicate checks.
- `src/web_task_agent/storage.py`: SQLite schema creation and repository methods.
- `src/web_task_agent/reporter.py`: Markdown report generation.
- `src/web_task_agent/workflow.py`: LangGraph workflow builder and fallback sequential runner.
- `src/web_task_agent/cli.py`: command-line entrypoint for the MVP demo.
- `tests/fixtures/job_pages.py`: fake pages used by tests.
- `tests/test_models.py`: model validation tests.
- `tests/test_extractor.py`: extraction tests.
- `tests/test_verifier.py`: verifier and dedupe tests.
- `tests/test_storage.py`: SQLite tests.
- `tests/test_reporter.py`: report rendering tests.
- `tests/test_workflow.py`: end-to-end workflow tests with fake browser.
- `tests/test_cli.py`: CLI smoke test.

## Scope Boundary

This first implementation plan covers Milestone 1 and Milestone 2 from the design spec. It produces a reliable local demo that can search over fake or configured browser pages, extract jobs, verify records, persist them, and write a Markdown report. Resume matching, Dashboard UI, and 20-task benchmark are intentionally deferred to a second implementation plan after this MVP is stable.

---

### Task 1: Project Scaffold

**Files:**
- Create: `D:\Agent\pyproject.toml`
- Create: `D:\Agent\.gitignore`
- Create: `D:\Agent\README.md`
- Create: `D:\Agent\src\web_task_agent\__init__.py`
- Create: `D:\Agent\tests\__init__.py`

- [ ] **Step 1: Create package metadata**

Create `D:\Agent\pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "web-task-agent"
version = "0.1.0"
description = "A browser-use and LangGraph based Web task agent for AI internship discovery."
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
  "browser-use>=0.1.40",
  "langgraph>=0.2.60",
  "pydantic>=2.7",
  "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2",
  "pytest-cov>=5.0",
]

[project.scripts]
web-task-agent = "web_task_agent.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-q"
```

- [ ] **Step 2: Create ignore rules**

Create `D:\Agent\.gitignore`:

```gitignore
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.coverage
htmlcov/
*.db
reports/
browser-artifacts/
.env
```

- [ ] **Step 3: Create README**

Create `D:\Agent\README.md`:

```markdown
# Web 自动任务 Agent

这是一个面向 AI 工程 / AI 应用实习的 Agent 项目。第一版目标是基于 `browser-use` 和 `LangGraph` 构建 Web 自动任务工作流，自动发现 AI 实习岗位，抽取结构化 JD，验证结果并生成 Markdown 报告。

## MVP 能力

- 通过浏览器客户端读取网页内容。
- 用工作流拆分规划、浏览、抽取、验证、保存和报告生成。
- 用 SQLite 保存岗位记录和运行指标。
- 用测试中的 fake browser 保证端到端流程可复现。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
web-task-agent --keyword "AI engineering intern" --location "Remote" --target-count 3
```
```

- [ ] **Step 4: Create package marker**

Create `D:\Agent\src\web_task_agent\__init__.py`:

```python
"""Web task agent package."""

__version__ = "0.1.0"
```

- [ ] **Step 5: Create tests package marker**

Create `D:\Agent\tests\__init__.py`:

```python
"""Tests for web_task_agent."""
```

- [ ] **Step 6: Install dependencies**

Run:

```powershell
pip install -e ".[dev]"
```

Expected: package installs successfully and `pytest --version` prints a pytest version.

- [ ] **Step 7: Commit scaffold**

Run:

```powershell
git add pyproject.toml .gitignore README.md src/web_task_agent/__init__.py tests/__init__.py
git commit -m "chore: scaffold web task agent project"
```

Expected: commit succeeds.

---

### Task 2: Domain Models

**Files:**
- Create: `D:\Agent\src\web_task_agent\models.py`
- Test: `D:\Agent\tests\test_models.py`

- [ ] **Step 1: Write failing model tests**

Create `D:\Agent\tests\test_models.py`:

```python
from web_task_agent.models import (
    BrowserPage,
    JobPosting,
    RunMetrics,
    UserProfile,
    WorkflowState,
)


def test_job_posting_normalizes_skills_and_confidence():
    job = JobPosting(
        title="AI Engineering Intern",
        company="Example AI",
        location="Remote",
        source="fixture",
        url="https://example.com/jobs/1",
        requirements="Python, LLM, RAG",
        responsibilities="Build AI agents",
        skills=["Python", " python ", "LLM"],
        posted_at="2026-06-07",
        confidence=1.2,
    )

    assert job.skills == ["Python", "LLM"]
    assert job.confidence == 1.0


def test_workflow_state_tracks_pages_jobs_and_metrics():
    state = WorkflowState(
        user=UserProfile(
            keyword="AI intern",
            location="Remote",
            target_count=2,
            skills=["Python", "LangGraph"],
        )
    )
    state.pages.append(
        BrowserPage(
            url="https://example.com/jobs",
            title="Jobs",
            content="AI Engineering Intern at Example AI",
        )
    )
    state.jobs.append(
        JobPosting(
            title="AI Engineering Intern",
            company="Example AI",
            location="Remote",
            source="fixture",
            url="https://example.com/jobs/1",
            requirements="Python",
            responsibilities="Build agents",
            skills=["Python"],
        )
    )
    state.metrics = RunMetrics(run_id="run-1", pages_visited=1, jobs_found=1, valid_jobs=1)

    assert state.user.target_count == 2
    assert state.pages[0].title == "Jobs"
    assert state.jobs[0].company == "Example AI"
    assert state.metrics.valid_jobs == 1
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/test_models.py -q
```

Expected: FAIL with `ModuleNotFoundError` or missing `web_task_agent.models`.

- [ ] **Step 3: Implement models**

Create `D:\Agent\src\web_task_agent\models.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _unique_clean_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        item = value.strip()
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            cleaned.append(item)
    return cleaned


class UserProfile(BaseModel):
    keyword: str
    location: str = "Remote"
    target_count: int = Field(default=10, ge=1, le=50)
    skills: list[str] = Field(default_factory=list)
    resume_text: str = ""

    @field_validator("keyword", "location")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value

    @field_validator("skills")
    @classmethod
    def normalize_skills(cls, values: list[str]) -> list[str]:
        return _unique_clean_strings(values)


class BrowserPage(BaseModel):
    url: str
    title: str = ""
    content: str
    source: str = "browser"
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobPosting(BaseModel):
    title: str
    company: str
    location: str
    source: str
    url: str
    requirements: str = ""
    responsibilities: str = ""
    skills: list[str] = Field(default_factory=list)
    posted_at: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("title", "company", "location", "source", "url")
    @classmethod
    def strip_core_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("core job fields must not be empty")
        return value

    @field_validator("skills")
    @classmethod
    def normalize_skills(cls, values: list[str]) -> list[str]:
        return _unique_clean_strings(values)


class MatchResult(BaseModel):
    job_id: str
    score: float = Field(ge=0.0, le=1.0)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    reason: str = ""
    priority: str = "medium"
    suggested_actions: list[str] = Field(default_factory=list)


class RunMetrics(BaseModel):
    run_id: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    pages_visited: int = 0
    jobs_found: int = 0
    valid_jobs: int = 0
    duplicate_jobs: int = 0
    failed_pages: int = 0
    avg_steps_per_job: float = 0.0
    estimated_token_cost: float = 0.0


class WorkflowState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user: UserProfile
    search_queries: list[str] = Field(default_factory=list)
    candidate_urls: list[str] = Field(default_factory=list)
    pages: list[BrowserPage] = Field(default_factory=list)
    jobs: list[JobPosting] = Field(default_factory=list)
    failed_urls: list[str] = Field(default_factory=list)
    metrics: RunMetrics | None = None
    report_path: str | None = None
```

- [ ] **Step 4: Run model tests**

Run:

```powershell
pytest tests/test_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit models**

Run:

```powershell
git add src/web_task_agent/models.py tests/test_models.py
git commit -m "feat: add domain models"
```

Expected: commit succeeds.

---

### Task 3: Browser Client Boundary

**Files:**
- Create: `D:\Agent\src\web_task_agent\browser.py`
- Create: `D:\Agent\tests\fixtures\job_pages.py`
- Test: `D:\Agent\tests\test_browser.py`

- [ ] **Step 1: Write browser tests**

Create `D:\Agent\tests\fixtures\job_pages.py`:

```python
from web_task_agent.models import BrowserPage


FAKE_JOB_PAGES = [
    BrowserPage(
        url="https://example.com/jobs/ai-engineering-intern",
        title="AI Engineering Intern",
        content=(
            "Title: AI Engineering Intern\n"
            "Company: Example AI\n"
            "Location: Remote\n"
            "Requirements: Python, LangGraph, LLM\n"
            "Responsibilities: Build browser agents and evaluation tools\n"
            "Posted: 2026-06-07\n"
        ),
        source="fixture",
    ),
    BrowserPage(
        url="https://example.com/jobs/ml-platform-intern",
        title="ML Platform Intern",
        content=(
            "Title: ML Platform Intern\n"
            "Company: DataWorks\n"
            "Location: Shanghai\n"
            "Requirements: Python, FastAPI, SQL\n"
            "Responsibilities: Build internal AI services\n"
            "Posted: 2026-06-06\n"
        ),
        source="fixture",
    ),
]
```

Create `D:\Agent\tests\test_browser.py`:

```python
import pytest

from tests.fixtures.job_pages import FAKE_JOB_PAGES
from web_task_agent.browser import BrowserUseClient, FakeBrowserClient


@pytest.mark.asyncio
async def test_fake_browser_search_returns_matching_pages():
    client = FakeBrowserClient(FAKE_JOB_PAGES)

    pages = await client.search("AI intern", target_count=1)

    assert len(pages) == 1
    assert pages[0].title == "AI Engineering Intern"


@pytest.mark.asyncio
async def test_fake_browser_open_url_returns_exact_page():
    client = FakeBrowserClient(FAKE_JOB_PAGES)

    page = await client.open_url("https://example.com/jobs/ml-platform-intern")

    assert page.company is None if hasattr(page, "company") else True
    assert page.title == "ML Platform Intern"


def test_browser_use_client_is_constructible_without_launching_browser():
    client = BrowserUseClient()

    assert client.default_timeout_seconds == 60
```

- [ ] **Step 2: Add pytest async dependency**

Modify `D:\Agent\pyproject.toml` dev dependencies:

```toml
[project.optional-dependencies]
dev = [
  "pytest>=8.2",
  "pytest-asyncio>=0.23",
  "pytest-cov>=5.0",
]
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```powershell
pytest tests/test_browser.py -q
```

Expected: FAIL because `web_task_agent.browser` does not exist.

- [ ] **Step 4: Implement browser client boundary**

Create `D:\Agent\src\web_task_agent\browser.py`:

```python
from __future__ import annotations

from typing import Protocol

from web_task_agent.models import BrowserPage


class BrowserClient(Protocol):
    async def search(self, query: str, target_count: int) -> list[BrowserPage]:
        """Return pages relevant to a query."""

    async def open_url(self, url: str) -> BrowserPage:
        """Open one URL and return readable page content."""


class FakeBrowserClient:
    def __init__(self, pages: list[BrowserPage]):
        self._pages = pages

    async def search(self, query: str, target_count: int) -> list[BrowserPage]:
        terms = [term.lower() for term in query.split() if term.strip()]
        scored: list[tuple[int, BrowserPage]] = []
        for page in self._pages:
            haystack = f"{page.title}\n{page.content}".lower()
            score = sum(1 for term in terms if term in haystack)
            if score > 0:
                scored.append((score, page))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [page for _, page in scored[:target_count]]

    async def open_url(self, url: str) -> BrowserPage:
        for page in self._pages:
            if page.url == url:
                return page
        raise ValueError(f"URL not found in fake browser: {url}")


class BrowserUseClient:
    def __init__(self, default_timeout_seconds: int = 60):
        self.default_timeout_seconds = default_timeout_seconds

    async def search(self, query: str, target_count: int) -> list[BrowserPage]:
        raise NotImplementedError(
            "Real browser-use search will be implemented after the deterministic MVP passes tests."
        )

    async def open_url(self, url: str) -> BrowserPage:
        raise NotImplementedError(
            "Real browser-use URL opening will be implemented after the deterministic MVP passes tests."
        )
```

- [ ] **Step 5: Run browser tests**

Run:

```powershell
pytest tests/test_browser.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit browser boundary**

Run:

```powershell
git add pyproject.toml src/web_task_agent/browser.py tests/fixtures/job_pages.py tests/test_browser.py
git commit -m "feat: add browser client boundary"
```

Expected: commit succeeds.

---

### Task 4: Page Extractor

**Files:**
- Create: `D:\Agent\src\web_task_agent\extractor.py`
- Test: `D:\Agent\tests\test_extractor.py`

- [ ] **Step 1: Write extractor tests**

Create `D:\Agent\tests\test_extractor.py`:

```python
from tests.fixtures.job_pages import FAKE_JOB_PAGES
from web_task_agent.extractor import PageExtractor


def test_extract_job_from_labeled_page_content():
    extractor = PageExtractor()

    job = extractor.extract(FAKE_JOB_PAGES[0])

    assert job.title == "AI Engineering Intern"
    assert job.company == "Example AI"
    assert job.location == "Remote"
    assert job.skills == ["Python", "LangGraph", "LLM"]
    assert job.posted_at == "2026-06-07"
    assert job.confidence >= 0.8


def test_extract_job_uses_page_title_when_title_label_missing():
    page = FAKE_JOB_PAGES[0].model_copy(
        update={
            "title": "Fallback AI Intern",
            "content": (
                "Company: Example AI\n"
                "Location: Remote\n"
                "Requirements: Python, LLM\n"
                "Responsibilities: Build AI tools\n"
            ),
        }
    )
    extractor = PageExtractor()

    job = extractor.extract(page)

    assert job.title == "Fallback AI Intern"
    assert job.confidence >= 0.6
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/test_extractor.py -q
```

Expected: FAIL because `web_task_agent.extractor` does not exist.

- [ ] **Step 3: Implement extractor**

Create `D:\Agent\src\web_task_agent\extractor.py`:

```python
from __future__ import annotations

from web_task_agent.models import BrowserPage, JobPosting


class PageExtractor:
    FIELD_ALIASES = {
        "title": ["Title", "Job Title", "Position"],
        "company": ["Company", "Employer"],
        "location": ["Location", "City"],
        "requirements": ["Requirements", "Skills"],
        "responsibilities": ["Responsibilities", "Role"],
        "posted_at": ["Posted", "Posted At", "Date"],
    }

    def extract(self, page: BrowserPage) -> JobPosting:
        fields = self._extract_labeled_fields(page.content)
        title = fields.get("title") or page.title
        company = fields.get("company") or "Unknown Company"
        location = fields.get("location") or "Unknown Location"
        requirements = fields.get("requirements") or ""
        responsibilities = fields.get("responsibilities") or ""
        skills = self._extract_skills(requirements)
        confidence = self._confidence(
            {
                "title": title,
                "company": company if company != "Unknown Company" else "",
                "location": location if location != "Unknown Location" else "",
                "requirements": requirements,
                "responsibilities": responsibilities,
            }
        )

        return JobPosting(
            title=title,
            company=company,
            location=location,
            source=page.source,
            url=page.url,
            requirements=requirements,
            responsibilities=responsibilities,
            skills=skills,
            posted_at=fields.get("posted_at", ""),
            confidence=confidence,
        )

    def _extract_labeled_fields(self, content: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for raw_line in content.splitlines():
            if ":" not in raw_line:
                continue
            label, value = raw_line.split(":", 1)
            label = label.strip().lower()
            value = value.strip()
            if not value:
                continue
            for field, aliases in self.FIELD_ALIASES.items():
                if label in {alias.lower() for alias in aliases}:
                    result[field] = value
        return result

    def _extract_skills(self, requirements: str) -> list[str]:
        parts = requirements.replace("，", ",").split(",")
        return [part.strip() for part in parts if part.strip()]

    def _confidence(self, fields: dict[str, str]) -> float:
        required = ["title", "company", "location", "requirements", "responsibilities"]
        present = sum(1 for key in required if fields.get(key))
        return round(present / len(required), 2)
```

- [ ] **Step 4: Run extractor tests**

Run:

```powershell
pytest tests/test_extractor.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit extractor**

Run:

```powershell
git add src/web_task_agent/extractor.py tests/test_extractor.py
git commit -m "feat: add page extractor"
```

Expected: commit succeeds.

---

### Task 5: Verifier

**Files:**
- Create: `D:\Agent\src\web_task_agent\verifier.py`
- Test: `D:\Agent\tests\test_verifier.py`

- [ ] **Step 1: Write verifier tests**

Create `D:\Agent\tests\test_verifier.py`:

```python
from web_task_agent.models import JobPosting
from web_task_agent.verifier import JobVerifier


def make_job(**overrides):
    data = {
        "title": "AI Engineering Intern",
        "company": "Example AI",
        "location": "Remote",
        "source": "fixture",
        "url": "https://example.com/jobs/1",
        "requirements": "Python, LangGraph, LLM",
        "responsibilities": "Build AI agents",
        "skills": ["Python", "LangGraph", "LLM"],
        "confidence": 0.9,
    }
    data.update(overrides)
    return JobPosting(**data)


def test_verifier_accepts_relevant_complete_job():
    verifier = JobVerifier(required_keywords=["AI", "LLM", "Agent"])

    result = verifier.verify(make_job())

    assert result.is_valid is True
    assert result.reasons == []


def test_verifier_rejects_low_confidence_job():
    verifier = JobVerifier(required_keywords=["AI"])

    result = verifier.verify(make_job(confidence=0.3))

    assert result.is_valid is False
    assert "confidence below 0.5" in result.reasons


def test_dedupe_removes_same_company_title_pair():
    verifier = JobVerifier(required_keywords=["AI"])
    first = make_job(url="https://example.com/jobs/1")
    duplicate = make_job(url="https://example.com/jobs/2")

    unique, duplicates = verifier.dedupe([first, duplicate])

    assert unique == [first]
    assert duplicates == [duplicate]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/test_verifier.py -q
```

Expected: FAIL because `web_task_agent.verifier` does not exist.

- [ ] **Step 3: Implement verifier**

Create `D:\Agent\src\web_task_agent\verifier.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from web_task_agent.models import JobPosting


@dataclass(frozen=True)
class VerificationResult:
    is_valid: bool
    reasons: list[str]


class JobVerifier:
    def __init__(self, required_keywords: list[str] | None = None, min_confidence: float = 0.5):
        self.required_keywords = [keyword.lower() for keyword in (required_keywords or ["AI", "LLM", "Agent"])]
        self.min_confidence = min_confidence

    def verify(self, job: JobPosting) -> VerificationResult:
        reasons: list[str] = []
        if job.confidence < self.min_confidence:
            reasons.append(f"confidence below {self.min_confidence}")
        if not job.requirements and not job.responsibilities:
            reasons.append("missing requirements and responsibilities")
        haystack = " ".join(
            [
                job.title,
                job.requirements,
                job.responsibilities,
                " ".join(job.skills),
            ]
        ).lower()
        if self.required_keywords and not any(keyword in haystack for keyword in self.required_keywords):
            reasons.append("not relevant to AI internship direction")
        return VerificationResult(is_valid=not reasons, reasons=reasons)

    def dedupe(self, jobs: list[JobPosting]) -> tuple[list[JobPosting], list[JobPosting]]:
        seen: set[tuple[str, str]] = set()
        unique: list[JobPosting] = []
        duplicates: list[JobPosting] = []
        for job in jobs:
            key = (job.company.strip().lower(), job.title.strip().lower())
            if key in seen:
                duplicates.append(job)
            else:
                seen.add(key)
                unique.append(job)
        return unique, duplicates
```

- [ ] **Step 4: Run verifier tests**

Run:

```powershell
pytest tests/test_verifier.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit verifier**

Run:

```powershell
git add src/web_task_agent/verifier.py tests/test_verifier.py
git commit -m "feat: add job verifier"
```

Expected: commit succeeds.

---

### Task 6: SQLite Storage

**Files:**
- Create: `D:\Agent\src\web_task_agent\storage.py`
- Test: `D:\Agent\tests\test_storage.py`

- [ ] **Step 1: Write storage tests**

Create `D:\Agent\tests\test_storage.py`:

```python
from web_task_agent.models import JobPosting, RunMetrics
from web_task_agent.storage import JobRepository


def test_repository_saves_and_lists_jobs(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()
    job = JobPosting(
        title="AI Engineering Intern",
        company="Example AI",
        location="Remote",
        source="fixture",
        url="https://example.com/jobs/1",
        requirements="Python, LLM",
        responsibilities="Build agents",
        skills=["Python", "LLM"],
        confidence=0.9,
    )

    repo.save_jobs([job])
    jobs = repo.list_jobs()

    assert len(jobs) == 1
    assert jobs[0].title == "AI Engineering Intern"
    assert jobs[0].skills == ["Python", "LLM"]


def test_repository_saves_run_metrics(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()
    metrics = RunMetrics(run_id="run-1", pages_visited=2, jobs_found=2, valid_jobs=1)

    repo.save_run_metrics(metrics)
    loaded = repo.get_run_metrics("run-1")

    assert loaded is not None
    assert loaded.pages_visited == 2
    assert loaded.valid_jobs == 1
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/test_storage.py -q
```

Expected: FAIL because `web_task_agent.storage` does not exist.

- [ ] **Step 3: Implement storage**

Create `D:\Agent\src\web_task_agent\storage.py`:

```python
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from web_task_agent.models import JobPosting, RunMetrics


class JobRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    url TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT NOT NULL,
                    source TEXT NOT NULL,
                    requirements TEXT NOT NULL,
                    responsibilities TEXT NOT NULL,
                    skills_json TEXT NOT NULL,
                    posted_at TEXT NOT NULL,
                    confidence REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_metrics (
                    run_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    pages_visited INTEGER NOT NULL,
                    jobs_found INTEGER NOT NULL,
                    valid_jobs INTEGER NOT NULL,
                    duplicate_jobs INTEGER NOT NULL,
                    failed_pages INTEGER NOT NULL,
                    avg_steps_per_job REAL NOT NULL,
                    estimated_token_cost REAL NOT NULL
                )
                """
            )

    def save_jobs(self, jobs: list[JobPosting]) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO jobs (
                    url, title, company, location, source, requirements,
                    responsibilities, skills_json, posted_at, confidence
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        job.url,
                        job.title,
                        job.company,
                        job.location,
                        job.source,
                        job.requirements,
                        job.responsibilities,
                        json.dumps(job.skills, ensure_ascii=False),
                        job.posted_at,
                        job.confidence,
                    )
                    for job in jobs
                ],
            )

    def list_jobs(self) -> list[JobPosting]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT title, company, location, source, url, requirements,
                       responsibilities, skills_json, posted_at, confidence
                FROM jobs
                ORDER BY company, title
                """
            ).fetchall()
        return [
            JobPosting(
                title=row["title"],
                company=row["company"],
                location=row["location"],
                source=row["source"],
                url=row["url"],
                requirements=row["requirements"],
                responsibilities=row["responsibilities"],
                skills=json.loads(row["skills_json"]),
                posted_at=row["posted_at"],
                confidence=row["confidence"],
            )
            for row in rows
        ]

    def save_run_metrics(self, metrics: RunMetrics) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO run_metrics (
                    run_id, started_at, finished_at, pages_visited, jobs_found,
                    valid_jobs, duplicate_jobs, failed_pages, avg_steps_per_job,
                    estimated_token_cost
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metrics.run_id,
                    metrics.started_at.isoformat(),
                    metrics.finished_at.isoformat() if metrics.finished_at else None,
                    metrics.pages_visited,
                    metrics.jobs_found,
                    metrics.valid_jobs,
                    metrics.duplicate_jobs,
                    metrics.failed_pages,
                    metrics.avg_steps_per_job,
                    metrics.estimated_token_cost,
                ),
            )

    def get_run_metrics(self, run_id: str) -> RunMetrics | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM run_metrics WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return RunMetrics(
            run_id=row["run_id"],
            pages_visited=row["pages_visited"],
            jobs_found=row["jobs_found"],
            valid_jobs=row["valid_jobs"],
            duplicate_jobs=row["duplicate_jobs"],
            failed_pages=row["failed_pages"],
            avg_steps_per_job=row["avg_steps_per_job"],
            estimated_token_cost=row["estimated_token_cost"],
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
```

- [ ] **Step 4: Run storage tests**

Run:

```powershell
pytest tests/test_storage.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit storage**

Run:

```powershell
git add src/web_task_agent/storage.py tests/test_storage.py
git commit -m "feat: add sqlite job repository"
```

Expected: commit succeeds.

---

### Task 7: Markdown Reporter

**Files:**
- Create: `D:\Agent\src\web_task_agent\reporter.py`
- Test: `D:\Agent\tests\test_reporter.py`

- [ ] **Step 1: Write reporter tests**

Create `D:\Agent\tests\test_reporter.py`:

```python
from web_task_agent.models import JobPosting, RunMetrics, UserProfile
from web_task_agent.reporter import MarkdownReporter


def test_reporter_writes_markdown_report(tmp_path):
    reporter = MarkdownReporter(output_dir=tmp_path)
    user = UserProfile(keyword="AI intern", location="Remote", target_count=1, skills=["Python"])
    jobs = [
        JobPosting(
            title="AI Engineering Intern",
            company="Example AI",
            location="Remote",
            source="fixture",
            url="https://example.com/jobs/1",
            requirements="Python, LLM",
            responsibilities="Build agents",
            skills=["Python", "LLM"],
            confidence=0.9,
        )
    ]
    metrics = RunMetrics(run_id="run-1", pages_visited=1, jobs_found=1, valid_jobs=1)

    report_path = reporter.write_report(user=user, jobs=jobs, metrics=metrics)

    content = report_path.read_text(encoding="utf-8")
    assert "# AI 实习岗位搜索报告" in content
    assert "AI Engineering Intern" in content
    assert "https://example.com/jobs/1" in content
    assert "有效岗位数: 1" in content
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/test_reporter.py -q
```

Expected: FAIL because `web_task_agent.reporter` does not exist.

- [ ] **Step 3: Implement reporter**

Create `D:\Agent\src\web_task_agent\reporter.py`:

```python
from __future__ import annotations

from pathlib import Path

from web_task_agent.models import JobPosting, RunMetrics, UserProfile


class MarkdownReporter:
    def __init__(self, output_dir: str | Path = "reports"):
        self.output_dir = Path(output_dir)

    def write_report(self, user: UserProfile, jobs: list[JobPosting], metrics: RunMetrics) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        report_path = self.output_dir / f"{metrics.run_id}.md"
        report_path.write_text(
            self.render(user=user, jobs=jobs, metrics=metrics),
            encoding="utf-8",
        )
        return report_path

    def render(self, user: UserProfile, jobs: list[JobPosting], metrics: RunMetrics) -> str:
        lines = [
            "# AI 实习岗位搜索报告",
            "",
            "## 搜索条件",
            "",
            f"- 关键词: {user.keyword}",
            f"- 地点: {user.location}",
            f"- 目标数量: {user.target_count}",
            f"- 技能标签: {', '.join(user.skills) if user.skills else '未提供'}",
            "",
            "## 运行指标",
            "",
            f"- 访问页面数: {metrics.pages_visited}",
            f"- 发现岗位数: {metrics.jobs_found}",
            f"- 有效岗位数: {metrics.valid_jobs}",
            f"- 重复岗位数: {metrics.duplicate_jobs}",
            f"- 失败页面数: {metrics.failed_pages}",
            "",
            "## 岗位列表",
            "",
        ]
        for index, job in enumerate(jobs, start=1):
            lines.extend(
                [
                    f"### {index}. {job.title}",
                    "",
                    f"- 公司: {job.company}",
                    f"- 地点: {job.location}",
                    f"- 技能: {', '.join(job.skills) if job.skills else '未抽取'}",
                    f"- 置信度: {job.confidence:.2f}",
                    f"- 链接: {job.url}",
                    "",
                    "**岗位要求**",
                    "",
                    job.requirements or "未抽取",
                    "",
                    "**工作内容**",
                    "",
                    job.responsibilities or "未抽取",
                    "",
                ]
            )
        return "\n".join(lines)
```

- [ ] **Step 4: Run reporter tests**

Run:

```powershell
pytest tests/test_reporter.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit reporter**

Run:

```powershell
git add src/web_task_agent/reporter.py tests/test_reporter.py
git commit -m "feat: add markdown reporter"
```

Expected: commit succeeds.

---

### Task 8: Workflow Runner

**Files:**
- Create: `D:\Agent\src\web_task_agent\workflow.py`
- Test: `D:\Agent\tests\test_workflow.py`

- [ ] **Step 1: Write workflow test**

Create `D:\Agent\tests\test_workflow.py`:

```python
import pytest

from tests.fixtures.job_pages import FAKE_JOB_PAGES
from web_task_agent.browser import FakeBrowserClient
from web_task_agent.extractor import PageExtractor
from web_task_agent.models import UserProfile
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow


@pytest.mark.asyncio
async def test_workflow_runs_end_to_end_with_fake_browser(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()
    workflow = WebTaskWorkflow(
        browser=FakeBrowserClient(FAKE_JOB_PAGES),
        extractor=PageExtractor(),
        verifier=JobVerifier(required_keywords=["AI", "LLM", "Agent"]),
        repository=repo,
        reporter=MarkdownReporter(output_dir=tmp_path / "reports"),
    )

    state = await workflow.run(
        UserProfile(
            keyword="AI intern",
            location="Remote",
            target_count=2,
            skills=["Python", "LangGraph"],
        ),
        run_id="run-test",
    )

    assert state.metrics is not None
    assert state.metrics.pages_visited == 2
    assert state.metrics.valid_jobs >= 1
    assert len(repo.list_jobs()) >= 1
    assert state.report_path is not None
    assert "run-test.md" in state.report_path
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/test_workflow.py -q
```

Expected: FAIL because `web_task_agent.workflow` does not exist.

- [ ] **Step 3: Implement workflow runner**

Create `D:\Agent\src\web_task_agent\workflow.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from web_task_agent.browser import BrowserClient
from web_task_agent.extractor import PageExtractor
from web_task_agent.models import RunMetrics, UserProfile, WorkflowState
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier


class WebTaskWorkflow:
    def __init__(
        self,
        browser: BrowserClient,
        extractor: PageExtractor,
        verifier: JobVerifier,
        repository: JobRepository,
        reporter: MarkdownReporter,
    ):
        self.browser = browser
        self.extractor = extractor
        self.verifier = verifier
        self.repository = repository
        self.reporter = reporter

    async def run(self, user: UserProfile, run_id: str | None = None) -> WorkflowState:
        state = WorkflowState(user=user)
        state.metrics = RunMetrics(run_id=run_id or f"run-{uuid4().hex[:8]}")
        state.search_queries = self._plan_queries(user)

        for query in state.search_queries:
            pages = await self.browser.search(query, target_count=user.target_count)
            state.pages.extend(pages)
            if len(state.pages) >= user.target_count:
                break

        extracted = [self.extractor.extract(page) for page in state.pages]
        unique_jobs, duplicates = self.verifier.dedupe(extracted)
        valid_jobs = [job for job in unique_jobs if self.verifier.verify(job).is_valid]

        state.jobs = valid_jobs
        state.metrics.pages_visited = len(state.pages)
        state.metrics.jobs_found = len(extracted)
        state.metrics.valid_jobs = len(valid_jobs)
        state.metrics.duplicate_jobs = len(duplicates)
        state.metrics.failed_pages = len(state.failed_urls)
        state.metrics.avg_steps_per_job = (
            round(state.metrics.pages_visited / len(valid_jobs), 2) if valid_jobs else 0.0
        )
        state.metrics.finished_at = datetime.now(timezone.utc)

        self.repository.save_jobs(valid_jobs)
        self.repository.save_run_metrics(state.metrics)
        report_path = self.reporter.write_report(user=user, jobs=valid_jobs, metrics=state.metrics)
        state.report_path = str(report_path)
        return state

    def _plan_queries(self, user: UserProfile) -> list[str]:
        return [
            f"{user.keyword} {user.location}",
            f"{user.keyword} LangGraph browser agent internship",
            f"{user.keyword} LLM agent internship",
        ]
```

- [ ] **Step 4: Run workflow tests**

Run:

```powershell
pytest tests/test_workflow.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit workflow**

Run:

```powershell
git add src/web_task_agent/workflow.py tests/test_workflow.py
git commit -m "feat: add web task workflow"
```

Expected: commit succeeds.

---

### Task 9: CLI Demo Entrypoint

**Files:**
- Create: `D:\Agent\src\web_task_agent\cli.py`
- Test: `D:\Agent\tests\test_cli.py`

- [ ] **Step 1: Write CLI test**

Create `D:\Agent\tests\test_cli.py`:

```python
from pathlib import Path

from web_task_agent.cli import main


def test_cli_demo_mode_writes_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "--keyword",
            "AI intern",
            "--location",
            "Remote",
            "--target-count",
            "2",
            "--demo",
        ]
    )

    assert exit_code == 0
    reports = list(Path("reports").glob("*.md"))
    assert len(reports) == 1
    assert "AI 实习岗位搜索报告" in reports[0].read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/test_cli.py -q
```

Expected: FAIL because `web_task_agent.cli` does not exist.

- [ ] **Step 3: Implement CLI**

Create `D:\Agent\src\web_task_agent\cli.py`:

```python
from __future__ import annotations

import argparse
import asyncio

from tests.fixtures.job_pages import FAKE_JOB_PAGES
from web_task_agent.browser import BrowserUseClient, FakeBrowserClient
from web_task_agent.extractor import PageExtractor
from web_task_agent.models import UserProfile
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Web Task Agent MVP.")
    parser.add_argument("--keyword", required=True)
    parser.add_argument("--location", default="Remote")
    parser.add_argument("--target-count", type=int, default=10)
    parser.add_argument("--skill", action="append", default=[])
    parser.add_argument("--demo", action="store_true", help="Use deterministic fake browser pages.")
    parser.add_argument("--db-path", default="agent.db")
    parser.add_argument("--report-dir", default="reports")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> int:
    repo = JobRepository(args.db_path)
    repo.initialize()
    browser = FakeBrowserClient(FAKE_JOB_PAGES) if args.demo else BrowserUseClient()
    workflow = WebTaskWorkflow(
        browser=browser,
        extractor=PageExtractor(),
        verifier=JobVerifier(required_keywords=["AI", "LLM", "Agent"]),
        repository=repo,
        reporter=MarkdownReporter(args.report_dir),
    )
    state = await workflow.run(
        UserProfile(
            keyword=args.keyword,
            location=args.location,
            target_count=args.target_count,
            skills=args.skill,
        )
    )
    print(f"Report written to: {state.report_path}")
    print(f"Valid jobs: {state.metrics.valid_jobs if state.metrics else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Move fixtures out of tests for runtime use**

Create `D:\Agent\src\web_task_agent\demo_pages.py`:

```python
from web_task_agent.models import BrowserPage


DEMO_JOB_PAGES = [
    BrowserPage(
        url="https://example.com/jobs/ai-engineering-intern",
        title="AI Engineering Intern",
        content=(
            "Title: AI Engineering Intern\n"
            "Company: Example AI\n"
            "Location: Remote\n"
            "Requirements: Python, LangGraph, LLM\n"
            "Responsibilities: Build browser agents and evaluation tools\n"
            "Posted: 2026-06-07\n"
        ),
        source="demo",
    ),
    BrowserPage(
        url="https://example.com/jobs/ml-platform-intern",
        title="ML Platform Intern",
        content=(
            "Title: ML Platform Intern\n"
            "Company: DataWorks\n"
            "Location: Shanghai\n"
            "Requirements: Python, FastAPI, SQL\n"
            "Responsibilities: Build internal AI services\n"
            "Posted: 2026-06-06\n"
        ),
        source="demo",
    ),
]
```

Modify `D:\Agent\tests\fixtures\job_pages.py`:

```python
from web_task_agent.demo_pages import DEMO_JOB_PAGES

FAKE_JOB_PAGES = DEMO_JOB_PAGES
```

Modify the import in `D:\Agent\src\web_task_agent\cli.py`:

```python
from web_task_agent.demo_pages import DEMO_JOB_PAGES
```

Modify the browser construction in `D:\Agent\src\web_task_agent\cli.py`:

```python
browser = FakeBrowserClient(DEMO_JOB_PAGES) if args.demo else BrowserUseClient()
```

- [ ] **Step 5: Run CLI tests**

Run:

```powershell
pytest tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 6: Run full test suite**

Run:

```powershell
pytest
```

Expected: all tests PASS.

- [ ] **Step 7: Commit CLI**

Run:

```powershell
git add src/web_task_agent/cli.py src/web_task_agent/demo_pages.py tests/fixtures/job_pages.py tests/test_cli.py
git commit -m "feat: add demo cli entrypoint"
```

Expected: commit succeeds.

---

### Task 10: MVP Verification and Documentation

**Files:**
- Modify: `D:\Agent\README.md`
- Create: `D:\Agent\docs\mvp-verification.md`

- [ ] **Step 1: Run the demo command**

Run:

```powershell
web-task-agent --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo
```

Expected output includes:

```text
Report written to: reports/
Valid jobs: 1
```

- [ ] **Step 2: Verify report file exists**

Run:

```powershell
Get-ChildItem -Path reports -Filter *.md
```

Expected: one Markdown report file is listed.

- [ ] **Step 3: Verify database has jobs**

Run:

```powershell
@'
from web_task_agent.storage import JobRepository
repo = JobRepository("agent.db")
jobs = repo.list_jobs()
print(len(jobs))
print(jobs[0].title if jobs else "no jobs")
'@ | python -
```

Expected:

```text
1
AI Engineering Intern
```

- [ ] **Step 4: Update README with verified command**

Modify `D:\Agent\README.md` to include:

```markdown
## 已验证的 MVP 命令

```powershell
web-task-agent --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo
```

该命令使用内置 demo 页面运行，不依赖真实招聘网站，适合快速展示工作流闭环。
```

- [ ] **Step 5: Create verification notes**

Create `D:\Agent\docs\mvp-verification.md`:

```markdown
# MVP 验证记录

## 验证命令

```powershell
pytest
web-task-agent --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo
```

## 验证结果

- 单元测试通过。
- CLI demo 能生成 Markdown 报告。
- SQLite 中能读取到有效岗位记录。

## 当前限制

- 真实 browser-use 网页操作仍在 adapter 边界之后，尚未接入生产搜索。
- 简历匹配、Dashboard 和 20 任务评测集属于下一阶段。
```

- [ ] **Step 6: Commit verification docs**

Run:

```powershell
git add README.md docs/mvp-verification.md
git commit -m "docs: add mvp verification notes"
```

Expected: commit succeeds.

---

## Self-Review Checklist

- Spec coverage:
  - Project scaffold: Task 1.
  - Browser execution boundary: Task 3.
  - Structured extraction: Task 4.
  - Verification and dedupe: Task 5.
  - SQLite persistence: Task 6.
  - Markdown reporting: Task 7.
  - Workflow orchestration: Task 8.
  - CLI demo and local verification: Task 9 and Task 10.
- Deferred from spec by design:
  - Resume matching is deferred to the next plan.
  - Streamlit Dashboard is deferred to the next plan.
  - 20-task benchmark is deferred to the next plan.
  - Real browser-use search implementation is behind `BrowserUseClient` and deferred until deterministic MVP tests pass.
- Type consistency:
  - `UserProfile`, `BrowserPage`, `JobPosting`, `RunMetrics`, and `WorkflowState` are defined in Task 2 and reused consistently.
  - `FakeBrowserClient`, `PageExtractor`, `JobVerifier`, `JobRepository`, `MarkdownReporter`, and `WebTaskWorkflow` are introduced before later tasks import them.
