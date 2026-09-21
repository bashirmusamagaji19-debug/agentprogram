# Dynamic Location Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every run filter jobs by that run's current location input, while retaining multi-city matches and preserving unrestricted behavior for `全国`/`不限`.

**Architecture:** Add a stateless location-matching module that normalizes administrative suffixes, separators, wildcard values, unknown values, and remote aliases. Invoke it in the workflow verifier node after job-quality verification and before matching/persistence, recording rejected jobs in the existing diagnostics metadata.

**Tech Stack:** Python 3.11+, Pydantic models, pytest, Ruff, Streamlit/LangGraph workflow.

---

### Task 1: Stateless dynamic location matching

**Files:**
- Create: `src/web_task_agent/location_filter.py`
- Create: `tests/test_location_filter.py`

- [ ] **Step 1: Write failing parameterized tests**

Create `tests/test_location_filter.py` with cases proving that the requested value is an argument rather than a fixed city:

```python
import pytest

from web_task_agent.location_filter import location_matches


@pytest.mark.parametrize(
    ("requested", "actual"),
    [
        ("深圳", "深圳"),
        ("深圳", "深圳市"),
        ("深圳", "广东省-深圳市"),
        ("深圳", "深圳总部、北京、上海"),
        ("北京", "北京市、上海市"),
        ("上海", "上海市"),
        ("Remote", "远程"),
        ("远程", "Remote - China"),
        ("全国", "Unknown Location"),
        ("不限", "北京"),
    ],
)
def test_location_matches_dynamic_requested_location(requested, actual):
    assert location_matches(requested, actual) is True


@pytest.mark.parametrize(
    ("requested", "actual"),
    [
        ("深圳", "北京市"),
        ("北京", "深圳市"),
        ("上海", "Unknown Location"),
        ("深圳", ""),
        ("深圳", "Remote"),
    ],
)
def test_location_rejects_other_or_unknown_locations(requested, actual):
    assert location_matches(requested, actual) is False
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_location_filter.py -q
```

Expected: collection fails because `web_task_agent.location_filter` does not exist.

- [ ] **Step 3: Implement the minimal pure matcher**

Create `src/web_task_agent/location_filter.py` with:

```python
from __future__ import annotations

import re
import unicodedata

_SEPARATORS = re.compile(r"[,，、;/；|\\\-—\\s]+")
_WILDCARDS = {"全国", "不限", "任意", "any", "anywhere", "all"}
_UNKNOWN = {"", "unknown", "unknown location", "未知", "未标注"}
_REMOTE = {"remote", "远程"}
_SUFFIXES = ("特别行政区", "自治区", "自治州", "省", "市")


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _canonical_token(value: str) -> str:
    token = _normalize(value)
    if token in _REMOTE:
        return "remote"
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) > len(suffix):
            return token[: -len(suffix)]
    return token


def _tokens(value: str) -> list[str]:
    return [
        token
        for token in (_canonical_token(part) for part in _SEPARATORS.split(_normalize(value)))
        if token
    ]


def is_unknown_location(value: str) -> bool:
    return _normalize(value) in _UNKNOWN


def location_matches(requested_location: str, actual_location: str) -> bool:
    requested = _normalize(requested_location)
    if requested in _WILDCARDS:
        return True
    if is_unknown_location(actual_location):
        return False
    requested_tokens = _tokens(requested_location)
    actual_tokens = _tokens(actual_location)
    return bool(requested_tokens) and any(
        requested_token == actual_token or requested_token in actual_token
        for requested_token in requested_tokens
        for actual_token in actual_tokens
    )
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_location_filter.py -q
```

Expected: all location matcher cases pass.

- [ ] **Step 5: Commit the isolated matcher**

```powershell
git add src/web_task_agent/location_filter.py tests/test_location_filter.py
git commit -m "feat: add dynamic job location matcher"
```

### Task 2: Enforce location in the workflow

**Files:**
- Modify: `src/web_task_agent/workflow.py:220-268`
- Modify: `tests/test_workflow.py`

- [ ] **Step 1: Write a failing workflow regression test**

Add a test that builds a `WorkflowState` with valid Shenzhen, Beijing, multi-city, and unknown jobs, calls `_verifier_node`, and asserts only jobs matching the current `UserProfile.location` remain. Run the same workflow instance again with a Beijing user and assert the result changes to Beijing jobs, proving there is no cached fixed city.

The assertions must include:

```python
assert [job.location for job in shenzhen_state.jobs] == ["深圳", "深圳、北京"]
assert [job.location for job in beijing_state.jobs] == ["北京", "深圳、北京"]
assert any("location mismatch" in reason for item in shenzhen_state.metadata["filtered_jobs"] for reason in item["reasons"])
assert any("location unknown" in reason for item in shenzhen_state.metadata["filtered_jobs"] for reason in item["reasons"])
```

- [ ] **Step 2: Run the regression test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workflow.py::test_workflow_filters_by_each_runs_location -q
```

Expected: the Beijing and unknown jobs remain because `_verifier_node` currently performs only quality verification.

- [ ] **Step 3: Add dynamic filtering to `_verifier_node`**

Import `is_unknown_location` and `location_matches`. For each quality-valid job, keep it only when:

```python
location_matches(state.user.location, job.location)
```

Otherwise append the existing diagnostic shape with one reason:

```python
reason = (
    f"location unknown: requested {state.user.location}"
    if is_unknown_location(job.location)
    else f"location mismatch: requested {state.user.location}, got {job.location}"
)
```

Do not store the requested location on the workflow object or at module scope; always read `state.user.location` for the current run.

- [ ] **Step 4: Run focused and related tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_location_filter.py tests/test_workflow.py tests/test_streamlit_runner.py -q
```

Expected: all focused tests pass, including nationwide workflows that retain existing behavior.

- [ ] **Step 5: Commit workflow enforcement**

```powershell
git add src/web_task_agent/workflow.py tests/test_workflow.py
git commit -m "fix: enforce requested location in job results"
```

### Task 3: Verify UI behavior and document the incident

**Files:**
- Modify: `docs/work-log/2026-09-18-realtime-location-and-url-reliability.md`
- Runtime artifact: `streamlit-runs/<run-id>/result.json`

- [ ] **Step 1: Run full automated verification**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 2: Restart Streamlit**

Stop the existing managed Streamlit execution session and start:

```powershell
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

Expected: `http://127.0.0.1:8501/_stcore/health` returns `200 ok`.

- [ ] **Step 3: Run live location checks**

Run the official source pipeline once with `location="深圳"` and once with `location="北京"`. For every returned job, assert `location_matches(requested, job.location)` and confirm the result sets differ with the input. Also run `location="全国"` to confirm unrestricted behavior.

- [ ] **Step 4: Append the work log**

Record the root cause (location was display-only in official mode), the request-driven design, RED/GREEN tests, final commands, live run IDs, retained multi-city behavior, excluded unknown locations, and the boundary that strict filtering can return fewer than the requested target count.

- [ ] **Step 5: Commit documentation**

```powershell
git add docs/work-log/2026-09-18-realtime-location-and-url-reliability.md
git commit -m "docs: record dynamic location filtering fix"
```
