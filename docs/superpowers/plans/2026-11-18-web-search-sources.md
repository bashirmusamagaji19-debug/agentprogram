# Web search and verified sources implementation plan

> **For agentic workers:** Execute the following tasks inline in the existing isolated worktree, with a self-review and verification after each task. Steps use checkbox syntax for tracking.

**Goal:** Expose real web discovery through SearchJobsTool, allow 30 model-enabled runs per session, and offer a verified catalog retaining major employers with dated end-to-end evidence.

**Architecture:** Add an explicit `web_search` request mode backed by the existing DeepSeek Search API. Search runs inside the Agent rather than during source construction; its URLs are archived and passed through the existing extraction, condition and fresh-link gates. Keep official mode explicit, with a curated source registry populated only after live discovery/detail/link checks.

**Tech Stack:** Python, Pydantic, asyncio, existing DeepSeek Search client, Streamlit, LangGraph, pytest, browser-use/Chromium.

### Task 1: Establish baseline and live source evidence

- [x] Run `python -m pytest -p no:cacheprovider` before implementation; record existing failures separately.
- [x] Probe major employers including Xiaomi, Xiaohongshu, Xpeng, Meituan, NetEase, MiHoYo, NIO and Li Auto using `OfficialListSource(specs=[name], keyword="AI Agent 实习", location="全国").discover(limit=10)`.
- [x] Run each prospective retained source through the production detail and fresh-link gates with synthetic skills and no private resume. Save public summaries, sampled URLs, timestamps and failure categories in `docs/results/source-validation-20261118/`.
- [x] Verify company-to-recruitment-domain affiliation from official pages. Retain only sources whose sampled pipeline completes; document limited query/sample coverage.

### Task 2: Implement bounded web search with regression tests first

Files: `src/web_task_agent/web_search.py`, `agent_components.py`, `agent_service.py`, `streamlit_runner.py`, `tests/test_web_search.py`.

- [x] Write tests for a search-enabled request with no seed URLs, real SearchJobsTool execution, deduped successive batches, search exception vs empty results, detail loading, request deadline, and the shared 10-candidate inspection cap.
- [x] Confirm tests fail because `web_search` is not accepted / the adapter does not exist.
- [x] Implement `WebSearchBrowser` using an injected `DeepSeekWebClient`; call synchronous API work in `asyncio.to_thread` with the remaining deadline. Use up to three query variants, keeping all user conditions unchanged and only adding recruitment/site hints. Search summaries remain hints, not verified JD facts.
- [x] Construct an empty seed list for `web_search`; expose search diagnostics and candidate summaries; route discovered links through official/HTTP/browser detail loading. Preserve SourceCaptureBrowser and fresh link verification.
- [x] Add `web_search` to the shared request/result type, require DEEPSEEK_API_KEY, and give the browser the shared request deadline. Preserve detail gates and isolate multi-source discovery deadlines.
- [x] Run the new tests plus browser, source capture and orchestration tests.

### Task 3: Expose UI and increase budget

Files: `streamlit_app.py`, `tests/test_streamlit_app.py`, `tests/test_streamlit_app_ui.py` (if present).

- [x] Update the budget regression to allow exactly 30 calls and reject the 31st; add web-search hint and budget tests before implementing.
- [x] Add `网页搜索` to the data-mode choices and mapping. Explain that the configured DeepSeek Search key is used, summaries are discovery hints, and returned jobs still require live verification.
- [x] Set `LLM_SESSION_LIMIT = 30`; include web search in billable run accounting and show remaining allowance. Keep the same session counter so an already-used three-run session can continue.
- [x] Replace the 42-option UI list with the curated verified registry and show dated verification scope/limits. Preserve legacy backend adapters and old-run loading.
- [x] Test that stale/unsupported UI source selections cannot leak back into a new request; keep source labels and API-vs-filter outcomes distinct.

### Task 4: Verification and deployment

- [x] Run Ruff, focused tests and the full deterministic suite with coverage. Final code master CI: 1224 passed, 1 skipped, coverage 88.22%; deployment-branch timing flakes and rerun are documented separately.
- [x] Self-review actual diff, request deadlines, candidate limits, search failure categories, public evidence and secret handling; independent review findings corrected.
- [x] Update README and `docs/work-log/2026-11-18-web-search-sources.md` with exact evidence, known source exclusions and limits.
- [x] Commit and push to the existing Streamlit deployment branches, preserving the user's dirty primary checkout.
- [x] Verify the deployed search mode, allowance 30, curated list, real SearchJobsTool trace and one freshly verified delivered job. Cloud-audit every retained source; remove Li Auto after repeated cloud timeouts. Final seven-source list and locale correction are pushed and Linux-verified; no subsequent manual UI check is claimed.
