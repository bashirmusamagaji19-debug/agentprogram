# Source Health and URL Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make official-source failures visible and prevent permanently removed job URLs from reaching user-facing results while preserving honest fallback for transient failures.

**Architecture:** Add a pure reliability classifier for exceptions and short page content, use it at the aggregator loading boundary, and record per-source discovery diagnostics without changing source isolation. Pass the structured diagnostics through the existing workflow metadata, JSON artifact, and Streamlit diagnostic table.

**Tech Stack:** Python 3.11, dataclasses, Pydantic, pytest/pytest-asyncio, LangGraph workflow state, Streamlit.

---

## File structure

- Create `src/web_task_agent/page_reliability.py`: stable failure categories, safe diagnostic text, exception/content classifiers, and the permanent-removal exception.
- Create `tests/test_page_reliability.py`: exhaustive unit tests for the pure classifier.
- Modify `src/web_task_agent/job_sources.py`: apply the classifier to official API and HTTP outcomes; stop fallback only for permanent removal; enrich `resolution_log`.
- Modify `tests/test_aggregator_loader.py`: prove permanent failures stop, temporary failures fall back, and normal paths keep their verification semantics.
- Modify `src/web_task_agent/official_list_source.py`: retain one merged diagnostic record per configured source for each discovery call.
- Modify `tests/test_official_list_source.py`: prove source isolation, empty/failed distinction, retry merging, reset behavior, and safe summaries.
- Modify `src/web_task_agent/streamlit_runner.py`: carry source and classified page diagnostics into workflow metadata before artifacts are written and merge them into `UiRunResult.diagnostics`.
- Modify `tests/test_streamlit_runner.py`: verify official diagnostics in JSON/UI results and absence in other modes.
- Modify `src/web_task_agent/streamlit_app.py`: make the empty-state copy cover both source and page diagnostics.
- Modify `tests/test_streamlit_app.py`: verify the expanded diagnostic categories and empty-state helper.
- Modify `README.md` and `docs/work-log/2026-09-18-realtime-location-and-url-reliability.md`: document the new reliability boundary and verification evidence.

### Task 1: Pure page reliability classification

**Files:**
- Create: `src/web_task_agent/page_reliability.py`
- Create: `tests/test_page_reliability.py`

- [ ] **Step 1: Write failing exception-classification tests**

Create `tests/test_page_reliability.py` with:

```python
from web_task_agent.browser import PageEmptyError, PageHttpError, PageTimeoutError
from web_task_agent.models import BrowserPage
from web_task_agent.page_reliability import (
    PageFailureCategory,
    classify_page_content,
    classify_page_exception,
    safe_diagnostic_detail,
)


def test_http_statuses_have_stable_failure_categories() -> None:
    assert classify_page_exception(PageHttpError("gone", 404)).category == PageFailureCategory.REMOVED
    assert classify_page_exception(PageHttpError("gone", 410)).category == PageFailureCategory.REMOVED
    assert classify_page_exception(PageHttpError("forbidden", 403)).category == PageFailureCategory.BLOCKED
    assert classify_page_exception(PageHttpError("limited", 429)).category == PageFailureCategory.BLOCKED
    assert classify_page_exception(PageHttpError("server", 503)).category == PageFailureCategory.TRANSIENT


def test_timeout_empty_and_unknown_have_stable_categories() -> None:
    assert classify_page_exception(PageTimeoutError("timeout")).category == PageFailureCategory.TRANSIENT
    assert classify_page_exception(PageEmptyError("empty")).category == PageFailureCategory.EMPTY_SHELL
    assert classify_page_exception(RuntimeError("boom")).category == PageFailureCategory.UNKNOWN


def test_known_unavailable_business_signal_is_removed() -> None:
    error = RuntimeError("official API says post unavailable (E1005)")
    assert classify_page_exception(error).category == PageFailureCategory.REMOVED


def test_safe_detail_redacts_credentials_and_limits_length() -> None:
    detail = safe_diagnostic_detail(
        "Authorization=Bearer secret-value api_key=another-secret " + "x" * 300
    )
    assert "secret-value" not in detail
    assert "another-secret" not in detail
    assert len(detail) <= 160
```

- [ ] **Step 2: Run the exception tests and verify the module is missing**

Run:

```powershell
\.venv\Scripts\python.exe -m pytest tests/test_page_reliability.py -v
```

Expected: FAIL during collection with `ModuleNotFoundError: web_task_agent.page_reliability`.

- [ ] **Step 3: Add failing content-classification tests**

Append to `tests/test_page_reliability.py`:

```python
def test_short_explicit_tombstone_is_removed() -> None:
    page = BrowserPage(
        url="https://example.com/jobs/1",
        title="职位已下线",
        content="该职位已下线，请查看其他职位。",
    )
    assert classify_page_content(page).category == PageFailureCategory.REMOVED


def test_normal_job_description_mentioning_phrase_is_not_removed() -> None:
    page = BrowserPage(
        url="https://example.com/jobs/2",
        title="平台研发实习生",
        content=(
            "岗位职责：负责处理页面不存在、接口超时等异常场景并完善监控。"
            "任职要求：熟悉 Python、HTTP、自动化测试和服务可靠性工程。"
        )
        * 4,
    )
    assert classify_page_content(page) is None


def test_short_non_tombstone_page_is_empty_shell() -> None:
    page = BrowserPage(
        url="https://example.com/jobs/3",
        title="招聘",
        content="请启用 JavaScript 后继续访问。",
    )
    assert classify_page_content(page).category == PageFailureCategory.EMPTY_SHELL


def test_long_page_is_live() -> None:
    page = BrowserPage(
        url="https://example.com/jobs/4",
        title="大模型实习生",
        content="岗位职责和任职要求。" * 30,
    )
    assert classify_page_content(page) is None
```

- [ ] **Step 4: Implement the pure reliability module**

Create `src/web_task_agent/page_reliability.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from web_task_agent.browser import (
    MIN_USEFUL_CONTENT_CHARS,
    PageEmptyError,
    PageHttpError,
    PageTimeoutError,
)
from web_task_agent.models import BrowserPage


class PageFailureCategory(StrEnum):
    REMOVED = "removed"
    BLOCKED = "blocked"
    TRANSIENT = "transient"
    EMPTY_SHELL = "empty_shell"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PageFailure:
    category: PageFailureCategory
    detail: str

    @property
    def is_permanent(self) -> bool:
        return self.category is PageFailureCategory.REMOVED


class PageRemovedError(RuntimeError):
    """The job detail URL has strong evidence of permanent removal."""


_TOMBSTONE_MARKERS = (
    "职位已下线",
    "岗位已下线",
    "职位不存在",
    "岗位不存在",
    "页面不存在",
    "job is no longer available",
    "position is no longer available",
)
_SECRET_PATTERN = re.compile(
    r"(?i)(authorization|api[-_ ]?key|access[-_ ]?token|token)\s*[:=]\s*\S+"
)


def safe_diagnostic_detail(value: object, *, max_chars: int = 160) -> str:
    normalized = " ".join(str(value).split())
    redacted = _SECRET_PATTERN.sub(r"\1=<redacted>", normalized)
    return redacted[:max_chars]


def classify_page_exception(exc: Exception) -> PageFailure:
    detail = safe_diagnostic_detail(exc)
    if isinstance(exc, PageHttpError):
        if exc.status_code in {404, 410}:
            return PageFailure(PageFailureCategory.REMOVED, detail)
        if exc.status_code in {401, 403, 429}:
            return PageFailure(PageFailureCategory.BLOCKED, detail)
        if exc.status_code is not None and exc.status_code >= 500:
            return PageFailure(PageFailureCategory.TRANSIENT, detail)
    if isinstance(exc, PageTimeoutError):
        return PageFailure(PageFailureCategory.TRANSIENT, detail)
    if isinstance(exc, PageEmptyError):
        return PageFailure(PageFailureCategory.EMPTY_SHELL, detail)
    if "E1005" in str(exc) or "post unavailable" in str(exc).casefold():
        return PageFailure(PageFailureCategory.REMOVED, detail)
    return PageFailure(PageFailureCategory.UNKNOWN, detail)


def classify_page_content(page: BrowserPage) -> PageFailure | None:
    content = page.content.strip()
    if len(content) >= MIN_USEFUL_CONTENT_CHARS:
        return None
    short_text = f"{page.title}\n{content}".casefold()
    if any(marker in short_text for marker in _TOMBSTONE_MARKERS):
        return PageFailure(PageFailureCategory.REMOVED, "explicit removal page")
    return PageFailure(PageFailureCategory.EMPTY_SHELL, "page below useful-content threshold")
```

- [ ] **Step 5: Run classifier tests**

Run:

```powershell
\.venv\Scripts\python.exe -m pytest tests/test_page_reliability.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit the classifier**

```powershell
git add src/web_task_agent/page_reliability.py tests/test_page_reliability.py
git commit -m "feat: classify job page reliability failures"
```

### Task 2: Enforce permanent-removal behavior in the aggregator loader

**Files:**
- Modify: `src/web_task_agent/job_sources.py:20-290`
- Modify: `tests/test_aggregator_loader.py`

- [ ] **Step 1: Write failing loader behavior tests**

Append to `tests/test_aggregator_loader.py`:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 410])
async def test_permanently_removed_http_page_does_not_fall_back_to_list_jd(jobs, status):
    from web_task_agent.browser import PageHttpError
    from web_task_agent.page_reliability import PageRemovedError

    class RemovedLoader:
        async def __call__(self, url):  # noqa: ANN001, ANN202
            raise PageHttpError(f"HTTP {status}: {url}", status_code=status)

    loader = AggregatorPageLoader(jobs, official_api=None, http_loader=RemovedLoader())

    with pytest.raises(PageRemovedError, match="removed"):
        await loader(jobs[0].url)
    assert loader.resolution_log[-1]["category"] == "removed"
    assert all(item["strategy"] != "jd_text-fallback" for item in loader.resolution_log)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,category", [(403, "blocked"), (429, "blocked"), (503, "transient")])
async def test_non_permanent_http_failure_keeps_list_jd_fallback(jobs, status, category):
    from web_task_agent.browser import PageHttpError

    class FailingLoader:
        async def __call__(self, url):  # noqa: ANN001, ANN202
            raise PageHttpError(f"HTTP {status}: {url}", status_code=status)

    loader = AggregatorPageLoader(jobs, official_api=None, http_loader=FailingLoader())

    page = await loader(jobs[0].url)

    assert page.source.startswith("aggregator:")
    assert loader.resolution_log[-2]["category"] == category
    assert loader.resolution_log[-1]["strategy"] == "jd_text-fallback"


@pytest.mark.asyncio
async def test_explicit_short_tombstone_does_not_fall_back_to_list_jd(jobs):
    from web_task_agent.page_reliability import PageRemovedError

    http = FakeHttpLoader(pages={jobs[0].url: "该职位已下线，请查看其他职位。"})
    loader = AggregatorPageLoader(jobs, official_api=None, http_loader=http)

    with pytest.raises(PageRemovedError):
        await loader(jobs[0].url)
    assert loader.resolution_log[-1]["category"] == "removed"


@pytest.mark.asyncio
async def test_official_api_short_tombstone_does_not_fall_back_to_list_jd(jobs):
    from web_task_agent.page_reliability import PageRemovedError

    class TombstoneOfficialApi:
        async def fetch(self, url):  # noqa: ANN001, ANN202
            class _Content:
                content = "该职位已下线，请查看其他职位。"
                title = "职位已下线"
                canonical_url = ""

            return _Content()

    loader = AggregatorPageLoader(jobs, official_api=TombstoneOfficialApi(), http_loader=None)

    with pytest.raises(PageRemovedError):
        await loader(jobs[0].url)
    assert loader.resolution_log[-1]["category"] == "removed"
```

- [ ] **Step 2: Run the focused loader tests and verify failure**

Run:

```powershell
\.venv\Scripts\python.exe -m pytest tests/test_aggregator_loader.py -k "permanent or non_permanent or tombstone" -v
```

Expected: FAIL because `PageRemovedError` and classified log fields are not wired into the loader.

- [ ] **Step 3: Integrate the classifier into `AggregatorPageLoader`**

Add imports near the top of `src/web_task_agent/job_sources.py`:

```python
from web_task_agent.page_reliability import (
    PageFailureCategory,
    PageRemovedError,
    classify_page_content,
    classify_page_exception,
)
```

Replace exception handling in the official API and HTTP branches with the following pattern, retaining the existing unsupported-domain special case:

```python
            except Exception as exc:  # noqa: BLE001
                if type(exc).__name__ == "UnsupportedOfficialApiError":
                    self._record(url, "official-api:unsupported")
                else:
                    failure = classify_page_exception(exc)
                    self._record(
                        url,
                        f"official-api-failed:{type(exc).__name__}",
                        category=failure.category,
                        detail=failure.detail,
                    )
                    if failure.is_permanent:
                        raise PageRemovedError(
                            f"job page removed: {url} ({failure.detail})"
                        ) from exc
```

In the official API success branch, build the page before accepting or rejecting short content, then classify it:

```python
                canonical = str(getattr(content, "canonical_url", "") or "").strip()
                page = self._to_page(url, content.content, content.title, "official-api")
                page = page.model_copy(
                    update={
                        "metadata": {
                            **page.metadata,
                            "content_origin": "official-api",
                            **({"canonical_url": canonical} if canonical else {}),
                        }
                    }
                )
                failure = classify_page_content(page)
                if failure is None:
                    self._cache(url, page)
                    self._record(url, "official-api")
                    return page
                self._record(
                    url,
                    "official-api:too-short",
                    category=failure.category,
                    detail=failure.detail,
                )
                if failure.is_permanent:
                    raise PageRemovedError(
                        f"job page removed: {url} ({failure.detail})"
                    )
```

For the HTTP success branch, classify the returned page before the length check:

```python
            except Exception as exc:  # noqa: BLE001
                failure = classify_page_exception(exc)
                self._record(
                    url,
                    f"http-failed:{type(exc).__name__}",
                    category=failure.category,
                    detail=failure.detail,
                )
                if failure.is_permanent:
                    raise PageRemovedError(
                        f"job page removed: {url} ({failure.detail})"
                    ) from exc
            else:
                failure = classify_page_content(page)
                if failure is not None:
                    self._record(
                        url,
                        "http:empty-page",
                        category=failure.category,
                        detail=failure.detail,
                    )
                    if failure.is_permanent:
                        raise PageRemovedError(
                            f"job page removed: {url} ({failure.detail})"
                        )
                else:
                    page = page.model_copy(
                        update={"metadata": {**page.metadata, "content_origin": "http"}}
                    )
                    self._cache(url, page)
                    self._record(url, "http")
                    return page
```

Replace `_record` with a backwards-compatible enriched form:

```python
    def _record(
        self,
        url: str,
        strategy: str,
        *,
        category: str | PageFailureCategory | None = None,
        detail: str = "",
    ) -> None:
        item = {"url": url, "strategy": strategy}
        if category:
            item["category"] = str(category)
        if detail:
            item["detail"] = detail
        self.resolution_log.append(item)
```

Update the annotation to `list[dict[str, str]]`; do not rename `strategy` or change successful log values.

- [ ] **Step 4: Run loader and URL-verification tests**

Run:

```powershell
\.venv\Scripts\python.exe -m pytest tests/test_aggregator_loader.py tests/test_url_verification.py -v
```

Expected: all tests PASS, including existing cache, canonical URL, `http-fetched`, `detail-probed`, and `list-attested` behavior.

- [ ] **Step 5: Commit loader behavior**

```powershell
git add src/web_task_agent/job_sources.py tests/test_aggregator_loader.py
git commit -m "fix: reject permanently removed job pages"
```

### Task 3: Record per-source discovery diagnostics

**Files:**
- Modify: `src/web_task_agent/official_list_source.py:1688-1750`
- Modify: `tests/test_official_list_source.py:1340-1420`

- [ ] **Step 1: Extend existing discovery tests with failing diagnostic assertions**

Add these assertions and tests near the existing fairness tests:

```python
    assert source.diagnostics == [
        {
            "source": "boom",
            "status": "failed",
            "attempts": 3,
            "jobs_discovered": 0,
            "error_type": "RuntimeError",
            "detail": "simulated SSL EOF",
        },
        {
            "source": "ok",
            "status": "healthy",
            "attempts": 3,
            "jobs_discovered": 1,
            "error_type": "",
            "detail": "",
        },
    ]


@pytest.mark.asyncio
async def test_discover_distinguishes_empty_source_and_resets_diagnostics(monkeypatch):
    import web_task_agent.official_list_source as ols

    calls = 0

    async def changing_lister(transport, limit):  # noqa: ARG001
        nonlocal calls
        calls += 1
        if calls <= 3:
            return []
        return [DiscoveredJob(url="https://ok.example.com/1", title="OK", source="changing")]

    monkeypatch.setattr(ols, "_SPECS", {"changing": changing_lister})
    source = ols.OfficialListSource(specs=["changing"], transport=object())

    assert await source.discover(limit=1) == []
    assert source.diagnostics[0]["status"] == "empty"
    jobs = await source.discover(limit=1)
    assert len(jobs) == 1
    assert source.diagnostics[0]["status"] == "healthy"
    assert source.diagnostics[0]["attempts"] == 1


@pytest.mark.asyncio
async def test_source_diagnostic_redacts_and_truncates_error(monkeypatch):
    import web_task_agent.official_list_source as ols

    async def secret_failure(transport, limit):  # noqa: ARG001
        raise RuntimeError("api_key=secret-value " + "x" * 300)

    monkeypatch.setattr(ols, "_SPECS", {"secret": secret_failure})
    source = ols.OfficialListSource(specs=["secret"], transport=object())

    await source.discover(limit=1)

    assert "secret-value" not in source.diagnostics[0]["detail"]
    assert len(source.diagnostics[0]["detail"]) <= 160
```

- [ ] **Step 2: Run the source tests and verify failure**

Run:

```powershell
\.venv\Scripts\python.exe -m pytest tests/test_official_list_source.py -k "discover_" -v
```

Expected: FAIL because `OfficialListSource` has no `diagnostics` state.

- [ ] **Step 3: Implement merged source diagnostics**

Import the sanitizer:

```python
from web_task_agent.page_reliability import safe_diagnostic_detail
```

Initialize public read-only-by-convention state in `OfficialListSource.__init__`:

```python
        self.diagnostics: list[dict[str, object]] = []
```

At the beginning of `discover`, reset it and create an ordered accumulator:

```python
        self.diagnostics = []
        diagnostic_by_source: dict[str, dict[str, object]] = {
            spec: {
                "source": spec,
                "status": "empty",
                "attempts": 0,
                "jobs_discovered": 0,
                "error_type": "",
                "detail": "",
            }
            for spec in self.specs
        }
```

Replace `_collect` with:

```python
        async def _collect(budget: int) -> list[list[DiscoveredJob]]:
            batches: list[list[DiscoveredJob]] = []
            for spec in self.specs:
                diagnostic = diagnostic_by_source[spec]
                diagnostic["attempts"] = int(diagnostic["attempts"]) + 1
                try:
                    per_source = await _SPECS[spec](self.transport, budget)
                except Exception as exc:  # noqa: BLE001
                    per_source = []
                    diagnostic.update(
                        status="failed",
                        jobs_discovered=0,
                        error_type=type(exc).__name__,
                        detail=safe_diagnostic_detail(exc),
                    )
                else:
                    diagnostic.update(
                        status="healthy" if per_source else "empty",
                        jobs_discovered=len(per_source),
                        error_type="",
                        detail="",
                    )
                batches.append(
                    [
                        job if job.tier else dataclasses.replace(job, tier=_SPEC_TIER.get(spec, ""))
                        for job in per_source
                    ]
                )
            self.diagnostics = [dict(diagnostic_by_source[spec]) for spec in self.specs]
            return batches
```

Keep the existing interleave and two補足 rounds unchanged so behavior and call counts remain stable.

- [ ] **Step 4: Run all official source tests**

Run:

```powershell
\.venv\Scripts\python.exe -m pytest tests/test_official_list_source.py -v
```

Expected: all tests PASS and existing fairness behavior remains unchanged.

- [ ] **Step 5: Commit source diagnostics**

```powershell
git add src/web_task_agent/official_list_source.py tests/test_official_list_source.py
git commit -m "feat: expose official source diagnostics"
```

### Task 4: Propagate source diagnostics into workflow state, JSON, and UI results

**Files:**
- Modify: `src/web_task_agent/streamlit_runner.py:155-315`
- Modify: `tests/test_streamlit_runner.py`

- [ ] **Step 1: Write failing integration tests with injected source construction**

Append to `tests/test_streamlit_runner.py`:

```python
@pytest.mark.asyncio
async def test_official_source_and_page_diagnostics_are_in_result_and_json(
    tmp_path, monkeypatch
) -> None:
    import json
    import web_task_agent.streamlit_runner as runner
    from web_task_agent.browser import PageHttpError
    from web_task_agent.job_sources import DiscoveredJob

    class FakeOfficialSource:
        def __init__(self, specs=None):  # noqa: ANN001
            self.diagnostics = []

        async def discover(self, limit):  # noqa: ANN001
            self.diagnostics = [
                {
                    "source": "broken",
                    "status": "failed",
                    "attempts": 1,
                    "jobs_discovered": 0,
                    "error_type": "RuntimeError",
                    "detail": "simulated failure",
                }
            ]
            return [
                DiscoveredJob(
                    url="https://example.com/jobs/1",
                    title="大模型算法实习生",
                    company="示例公司",
                    location="北京",
                    jd_text=(
                        "岗位职责：负责大模型训练、评测和推理优化。"
                        "任职要求：熟悉 Python、PyTorch 和 Transformer。"
                    )
                    * 4,
                    source="healthy",
                )
            ]

    async def blocked_page_loader(url):  # noqa: ANN001, ANN202
        raise PageHttpError(f"HTTP 403: {url}", status_code=403)

    monkeypatch.setattr(runner, "OfficialListSource", FakeOfficialSource)
    result = await run_ui_request(
        UiRunRequest(data_mode="official", official_specs=["broken"]),
        output_root=tmp_path,
        environ={},
        page_loader=blocked_page_loader,
    )

    assert result.jobs[0].url_verification == "list-attested"
    assert result.diagnostics[0] == {
        "url": "broken",
        "error": "source_failed: RuntimeError: simulated failure",
    }
    assert result.diagnostics[1]["url"] == "https://example.com/jobs/1"
    assert result.diagnostics[1]["error"].startswith(
        "page_blocked: http-failed:PageHttpError: HTTP 403"
    )
    payload = json.loads(result.artifacts["json"].read_text(encoding="utf-8"))
    assert payload["metadata"]["source_diagnostics"][0]["source"] == "broken"
    assert payload["metadata"]["page_diagnostics"][0]["category"] == "blocked"


@pytest.mark.asyncio
async def test_demo_mode_has_no_official_source_diagnostics(tmp_path) -> None:
    result = await run_ui_request(UiRunRequest(data_mode="demo"), output_root=tmp_path, environ={})
    payload = __import__("json").loads(result.artifacts["json"].read_text(encoding="utf-8"))
    assert "source_diagnostics" not in payload["metadata"]
    assert all(not item["error"].startswith("source_") for item in result.diagnostics)
```

- [ ] **Step 2: Run focused runner tests and verify failure**

Run:

```powershell
\.venv\Scripts\python.exe -m pytest tests/test_streamlit_runner.py -k "source_diagnostics" -v
```

Expected: FAIL because `_build_browser` currently discards source diagnostics.

- [ ] **Step 3: Return diagnostics from `_build_browser`**

Change its return annotation and every branch:

```python
async def _build_browser(
    request: UiRunRequest,
    *,
    repository: JobRepository,
    page_loader: Callable[[str], Awaitable[Any]] | None,
) -> tuple[
    FakeBrowserClient | BrowserUseClient,
    list[str],
    list[dict[str, object]],
    list[dict[str, str]],
]:
    if request.data_mode == "demo":
        return FakeBrowserClient(DEMO_JOB_PAGES), [], [], []
    if request.data_mode == "seed_urls":
        return (
            BrowserUseClient(page_loader=page_loader or HttpPageLoader()),
            request.seed_urls,
            [],
            [],
        )

    if request.data_mode == "official":
        source = OfficialListSource(specs=request.official_specs or None)
    else:
        source = AggregatorRepoSource(resolve_aggregator_location(request))
    jobs = await source.discover(limit=request.target_count)
    source_diagnostics = list(getattr(source, "diagnostics", []))
    loader = AggregatorPageLoader(
        jobs,
        official_api=OfficialApiContentFetcher(),
        http_loader=page_loader or HttpPageLoader(),
        repository=repository,
    )
    return (
        BrowserUseClient(page_loader=loader),
        [job.url for job in jobs],
        source_diagnostics,
        loader.resolution_log,
    )
```

- [ ] **Step 4: Add diagnostics before writing artifacts**

Unpack the third value near the start of `run_ui_request`:

```python
    browser, seed_urls, source_diagnostics, page_diagnostics = await _build_browser(
        request,
        repository=repository,
        page_loader=page_loader,
    )
```

Immediately after the workflow completes and before dashboard/JSON generation, attach it only when non-empty:

```python
    if source_diagnostics:
        state.metadata["source_diagnostics"] = source_diagnostics
    classified_page_diagnostics = [item for item in page_diagnostics if item.get("category")]
    if classified_page_diagnostics:
        state.metadata["page_diagnostics"] = classified_page_diagnostics
```

Add a focused formatter above `run_ui_request`:

```python
def _source_diagnostic_rows(
    diagnostics: list[dict[str, object]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in diagnostics:
        status = str(item.get("status", "unknown"))
        if status == "healthy":
            continue
        source = str(item.get("source", "unknown"))
        if status == "failed":
            error_type = str(item.get("error_type", "unknown"))
            detail = str(item.get("detail", ""))
            message = f"{error_type}: {detail}".rstrip(": ")
        else:
            message = "no jobs returned"
        rows.append({"url": source, "error": f"source_{status}: {message}"})
    return rows


def _page_diagnostic_rows(
    diagnostics: list[dict[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in diagnostics:
        category = item.get("category", "").strip()
        if not category:
            continue
        strategy = item.get("strategy", "unknown")
        detail = item.get("detail", "")
        message = f"{strategy}: {detail}".rstrip(": ")
        rows.append(
            {"url": item.get("url", ""), "error": f"page_{category}: {message}"}
        )
    return rows
```

Build final diagnostics as:

```python
    diagnostics = _source_diagnostic_rows(source_diagnostics)
    diagnostics.extend(_page_diagnostic_rows(classified_page_diagnostics))
    classified_urls = {item.get("url", "") for item in classified_page_diagnostics}
    diagnostics.extend(
        item
        for item in state.metadata.get("failed_url_errors", [])
        if item.get("url", "") not in classified_urls
    )
    diagnostics.extend(
        {
            "url": str(item.get("url", "")),
            "error": "verification_filtered: " + ", ".join(item.get("reasons", [])),
        }
        for item in state.metadata.get("filtered_jobs", [])
    )
```

- [ ] **Step 5: Run all runner tests**

Run:

```powershell
\.venv\Scripts\python.exe -m pytest tests/test_streamlit_runner.py -v
```

Expected: all tests PASS; official diagnostics exist in the JSON before it is written, while demo/seed/aggregator modes remain unchanged.

- [ ] **Step 6: Commit diagnostic propagation**

```powershell
git add src/web_task_agent/streamlit_runner.py tests/test_streamlit_runner.py
git commit -m "feat: surface source health in run diagnostics"
```

### Task 5: Make Streamlit diagnostic presentation match the expanded semantics

**Files:**
- Modify: `src/web_task_agent/streamlit_app.py:206-220,560-568`
- Modify: `tests/test_streamlit_app.py`

- [ ] **Step 1: Write failing presentation tests**

Add a small helper import and tests to `tests/test_streamlit_app.py`:

```python
from web_task_agent.streamlit_app import diagnostics_empty_message


def test_diagnostic_rows_render_source_and_page_categories(tmp_path: Path) -> None:
    result = make_result(tmp_path)
    result.diagnostics = [
        {"url": "agibot", "error": "source_failed: RuntimeError: API changed"},
        {"url": "https://example.com/dead", "error": "PageRemovedError: job page removed"},
    ]

    assert diagnostic_rows(result) == [
        {"类别": "source_failed", "目标": "agibot", "详情": "RuntimeError: API changed"},
        {
            "类别": "PageRemovedError",
            "目标": "https://example.com/dead",
            "详情": "job page removed",
        },
    ]


def test_empty_diagnostic_message_mentions_sources_and_pages() -> None:
    assert diagnostics_empty_message() == "本次运行没有来源或页面诊断。"
```

- [ ] **Step 2: Run focused app tests and verify the missing helper**

Run:

```powershell
\.venv\Scripts\python.exe -m pytest tests/test_streamlit_app.py -k "diagnostic" -v
```

Expected: FAIL importing `diagnostics_empty_message`.

- [ ] **Step 3: Add the helper and use it in the diagnostics tab**

Add near `diagnostic_rows`:

```python
def diagnostics_empty_message() -> str:
    return "本次运行没有来源或页面诊断。"
```

Replace the existing empty success message in `_render_result`:

```python
        else:
            st.success(diagnostics_empty_message())
```

- [ ] **Step 4: Run all Streamlit app tests**

Run:

```powershell
\.venv\Scripts\python.exe -m pytest tests/test_streamlit_app.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit UI semantics**

```powershell
git add src/web_task_agent/streamlit_app.py tests/test_streamlit_app.py
git commit -m "ui: distinguish source and page diagnostics"
```

### Task 6: Document the reliability boundary and run complete verification

**Files:**
- Modify: `README.md:271-280`
- Modify: `docs/work-log/2026-09-18-realtime-location-and-url-reliability.md`

- [ ] **Step 1: Add exact user-facing documentation**

Extend the existing README reliability note with:

```markdown
实时官方列表运行会在 JSON metadata 中记录每个配置来源的健康状态，并在
Streamlit“失败与诊断”页区分来源失败、空来源和页面失败。HTTP 404/410 或明确的
岗位下线页不会再用列表 JD 兜底为可点击结果；403、429、超时与 5xx 仍按临时或
受限故障处理，不会被误报为岗位下线。`list-attested` 仍只表示列表自证，不等于
逐页浏览器验证。
```

Append this section to the work log before running verification:

```markdown
## 四次复盘：来源健康度与永久失效链接门禁

官方列表模式现在会记录每个配置来源的最终状态、尝试次数、发现数量和脱敏后的失败摘要。单来源失败仍不会中断其他来源，但不会再与“正常返回空列表”混为一谈。

详情加载链新增稳定失败分类：404/410、E1005 或明确短下线页属于 `removed`，禁止用列表 JD 兜底；401/403/429 属于 `blocked`，5xx/超时属于 `transient`，短空壳属于 `empty_shell`，这些非永久信号仍允许诚实降级为 `list-attested`。分类结果会进入 JSON metadata 和 Streamlit“失败与诊断”页。

验证边界保持不变：`list-attested` 只证明岗位来自当次列表响应，不代表详情页已由真实浏览器完成渲染验证；临时网络故障也不等于岗位已经下线。
```

- [ ] **Step 2: Run focused reliability tests**

Run:

```powershell
\.venv\Scripts\python.exe -m pytest tests/test_page_reliability.py tests/test_aggregator_loader.py tests/test_official_list_source.py tests/test_streamlit_runner.py tests/test_streamlit_app.py tests/test_url_verification.py -v
```

Expected: all focused tests PASS.

- [ ] **Step 3: Run the complete test and lint gates**

Run each command separately and require exit code 0:

```powershell
\.venv\Scripts\python.exe -m pytest -q
\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

Expected: complete suite PASS, Ruff prints `All checks passed!`, and diff check prints no errors. Existing CRLF conversion warnings are informational only.

- [ ] **Step 4: Run the offline UI smoke**

Run:

```powershell
\.venv\Scripts\python.exe -c "import asyncio,tempfile; from pathlib import Path; from web_task_agent.streamlit_runner import UiRunRequest,run_ui_request; root=Path(tempfile.mkdtemp()); result=asyncio.run(run_ui_request(UiRunRequest(data_mode='demo',target_count=2),output_root=root,environ={})); assert len(result.jobs)==2; assert set(result.artifacts)=={'json','report','dashboard','action_plan'}; assert all(p.is_file() for p in result.artifacts.values()); print(result.run_id, len(result.jobs), sorted(result.artifacts))"
```

Expected: prints one run ID, `2`, and the four artifact names with exit code 0.

- [ ] **Step 5: Optionally run a bounded live official-source smoke**

Only if network access is available and the result can be labeled as a time-specific observation:

```powershell
\.venv\Scripts\python.exe -c "import asyncio; from web_task_agent.official_list_source import OfficialListSource; source=OfficialListSource(specs=['tencent-campus','xiaomi','agibot']); jobs=asyncio.run(source.discover(limit=6)); print('jobs',len(jobs)); print('diagnostics',source.diagnostics)"
```

Expected: command completes without a process-level exception. Individual sources may be `healthy`, `empty`, or `failed`; record the observed date and do not turn counts into deterministic assertions.

- [ ] **Step 6: Update the work log with actual verification evidence**

Append the following verification block. If Step 5 ran, add one additional bullet containing the command's exact printed `diagnostics` list; otherwise leave the three deterministic bullets unchanged:

```markdown
### 本轮自动验证

- 完整 `pytest -q`：退出码 0，本次收集的全部测试通过。
- `ruff check .` 与 `git diff --check`：退出码均为 0。
- 离线 Streamlit runner smoke：生成 2 个岗位和 JSON、Markdown、HTML Dashboard、行动计划四类产物。
```

Keep historical counts in older sections unchanged. Do not claim live-site success unless Step 5 actually ran.

- [ ] **Step 7: Commit documentation and verification record**

```powershell
git add README.md docs/work-log/2026-09-18-realtime-location-and-url-reliability.md
git commit -m "docs: record source health reliability evidence"
```

### Task 7: Final requirement audit

**Files:**
- Inspect: `docs/superpowers/specs/2026-09-19-source-health-and-url-reliability-design.md`
- Inspect: all files changed in Tasks 1-6

- [ ] **Step 1: Map every acceptance criterion to evidence**

Create a temporary checklist in the terminal output (not a repository file) mapping:

```text
1 source status in JSON -> runner integration test + result.json inspection
2 removed page cannot fall back -> aggregator 404/410/tombstone tests
3 blocked/transient can fall back -> aggregator 403/429/503 tests
4 UI distinguishes source/page -> Streamlit diagnostic-row tests
5 verification labels unchanged -> URL verification + existing app tests
6 gates and smoke pass -> full pytest, Ruff, diff check, offline smoke outputs
```

- [ ] **Step 2: Inspect the final diff and commit history**

Run:

```powershell
git status --short
git diff --stat HEAD~6..HEAD
git log -8 --oneline --decorate
```

Expected: only known pre-existing user changes remain uncommitted; all feature files are committed in focused commits. If commit count differs, use the actual feature base commit instead of assuming `HEAD~6`.

- [ ] **Step 3: Run final verification once more if any audit fix was required**

If the audit changed code or docs, rerun:

```powershell
\.venv\Scripts\python.exe -m pytest -q
\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 4: Use the verification-before-completion skill before reporting success**

Load `superpowers:verification-before-completion`, verify its checklist against fresh command output, and report the feature as complete only after every acceptance criterion has direct evidence.
