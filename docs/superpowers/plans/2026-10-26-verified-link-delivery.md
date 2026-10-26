# Verified link delivery

User-authorized follow-up to the real website usability plan and 2026-10-25 source audit.

## Root cause

`url_verification` records body provenance. Official API and list body fallbacks can pass extraction and persistence without visiting the displayed URL. Neither HTTP 200 nor a cached JD proves that a particular job detail is visible and accepting applications.

## Implementation

- [x] Independently visit the displayed URL within the existing run deadline. Inspect actual redirects, visible job identity, JD, and active application control. SPA rendering uses the existing browser adapter. Treat uncertainty or unconfirmed identity as pending and explicit removal as rejected.
- [x] Attach a receipt with run, aware timestamp, method, requested/final URL, identity/active checks, and body hash. Never reuse list/API/cache text as webpage evidence. Static HTTP alone cannot produce a verified delivery.
- [x] Enforce before counting a job in both hybrid and fixed workflows. Enforce again before persistence, including resumed approvals. Missing, expired, or previous-run receipts cannot be saved as verified delivery. Unsaved terminal results are not delivered, including CLI exports.
- [x] Preserve receipts in SQLite and exported JSON. Display pending candidates separately, show method/time on results, and label legacy provenance accurately. Public page evidence is downloadable as a ZIP.
- [x] Run regression tests for shell pages, redirects, closed jobs, blocked pages, receipt expiry, workflow bypasses, persistence and UI. Run one real unblocked official source and record the actual outcome without inflating delivery counts.

## Constraints

Do not change model configuration or keys. Do not send resume text to sources or verification requests. Do not bypass the prior browser access restriction on NetEase detail pages. Preserve existing dirty work and running worktree. Offline fixtures and explicit demo/replay runs remain offline and cannot acquire live verification labels.

## Evidence

Baseline source copies: `portfolio-artifacts/link-delivery-20261026/baseline/`.
Initial regression: eight failing tests in `tests/test_link_delivery_gate.py`; three reproduce saving without a current valid receipt.

Final acceptance: `docs/results/link-delivery-20261026/ACCEPTANCE.md`.
Full regression: **1105 passed, 1 skipped**. Ruff, wheel build, doctor and diff checks passed.
Final browser form run: **run-3de02cbd**, 3 independently browser-verified and saved jobs.
