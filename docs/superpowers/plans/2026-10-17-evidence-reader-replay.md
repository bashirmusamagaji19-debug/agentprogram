# Evidence-backed Web Reading Replay Implementation Plan

> **For agentic workers:** Execute inline with superpowers:executing-plans and test-driven-development; verify before completion. Preserve existing uncommitted work and production defaults.

**Goal:** Compare the current rule extractor against an LLM reader on identical saved page bodies, with auditable evidence and explicit uncertainty.

**Architecture:** Add an isolated typed reader that classifies page type and returns literal evidence for each job field. A deterministic gate checks quotations, missing fields, semantic role evidence and location conflicts before accepting anything. Replay the saved enhanced-arm pages; deduplicate identical URL/title/body model inputs, preserve per-city evaluation, record hashes, tokens, latency, errors and all decisions. No new web retrieval, following links, or application submissions.

**Tech Stack:** Python, existing DeepSeek-compatible transport/config, Pydantic, pytest.

### Task 1: Reader and deterministic evidence gate
- [x] Write tests for fabricated evidence, empty/missing fields, HR/company-description confusion, salary-as-company, conflicting cities, list pages, malformed model responses and proper evidence acceptance.
- [x] Observe missing-feature failures with `.venv/Scripts/python.exe -m pytest tests/test_evidence_reader.py`.
- [x] Implement `src/web_task_agent/evidence_reader.py` with strict structured output, source-only fields, safe errors and no implicit rule fallback.
- [x] Run tests and Ruff on new files.

### Task 2: Frozen-page replay
- [x] Write `tests/test_reading_replay.py` for identical inputs, hash-based reuse, per-case contexts, persistent errors and no snapshot mutation.
- [x] Observe failures before implementing `src/web_task_agent/reading_replay.py` and `scripts/compare_page_readers.py`.
- [x] Persist each model reading and incremental reports; cap calls and concurrency. Retain rejected/review/error records in the denominator.
- [x] Run targeted regression tests and Ruff.

### Task 3: Live-model evidence on saved content
- [x] Freeze audit labels for known HR, damaged conflicting-location and list-page examples before seeing model outputs.
- [x] Run DeepSeek on the 29 unique saved enhanced-arm inputs (34 per-city page records); use the original keyword and city filters for both readers.
- [x] Review controls and all LLM-accepted records against source evidence; report improvements and remaining problems without claiming general accuracy.
- [x] Save reports and work log, leave production defaults unchanged pending measured results.
