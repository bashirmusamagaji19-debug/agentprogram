# DeepSeek Search/Fetch Two-Arm Comparison Implementation Plan

> **For agentic workers:** Execute inline using superpowers:executing-plans, test-driven-development and verification-before-completion. Preserve the existing uncommitted baseline; add isolated files without changing production defaults.

**Goal:** Compare exactly two arms: the existing official-source pipeline, and that pipeline supplemented with DeepSeek Search and Fetch.

**Architecture:** Capture the same official candidates once per case. Evaluate each arm through the existing extractor, verifier, matcher and LangGraph workflow with separate repositories and a shared candidate cap. Supplemented candidates are interleaved with official candidates; Fetch is a bounded recovery path after the existing loader fails. Report all pipeline-valid candidates as well as the target-sized recommendation list. Pipeline validity is not independent ground truth or proof that a job remains open.

**Tech Stack:** Python, urllib, Pydantic, existing LangGraph workflow, pytest.

### Task 1: Safe API adapter
- [x] Add `tests/test_deepseek_search.py` for structured search, body-only Fetch, billable-call accounting, safe errors, and preservation of target-site removal signals.
- [x] Run `.venv/Scripts/python.exe -m pytest tests/test_deepseek_search.py` and observe missing-feature failures.
- [x] Add `src/web_task_agent/deepseek_search.py`: fixed official endpoints, injected transport, bounded requests, no secret/error-body persistence, Fetch errors distinct from target HTTP errors.
- [x] Re-run adapter tests.

### Task 2: Exactly two paired arms
- [x] Add `tests/test_search_comparison.py` for shared inputs, equal candidate caps, no API use in baseline, no summary-to-JD promotion, and report deltas.
- [x] Run targeted tests before implementing.
- [x] Add `src/web_task_agent/search_comparison.py` with paired official-candidate capture, source interleaving, page snapshots, per-arm metrics, incremental JSON and Markdown reports.
- [x] Add `scripts/compare_search_fetch.py` with bounded CLI options and environment-only key loading.
- [x] Run adapter/comparison and existing workflow/source regression tests, then Ruff on new files.

### Task 3: Live evidence
- [x] Run a bounded comparison for Beijing and Shanghai using the same explicit official sources (`tencent-campus,xiaomi`), keyword and candidate cap in both arms.
- [x] Preserve errors, empty results, API counts and estimated fees. Save under a new `portfolio-artifacts` directory; do not overwrite previous acceptance evidence.
- [x] Inspect results and describe measured differences and limits; do not extrapolate whole-web coverage or claim current production/UI integration.
