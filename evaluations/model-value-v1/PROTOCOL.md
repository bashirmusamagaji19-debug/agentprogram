# model-value-v1 C3 protocol (pre-registered)

This version is a frozen initial controlled dataset. It is synthetic and is not evidence about the
live job market. The current assistant labels are proposals: every case remains pending human review.
Formal test runs are blocked until an external reviewer writes `HUMAN_REVIEW_RECEIPT.json`
with a complete reviewed case list and attestation. Dev/exploratory use may load these tasks with
the explicit label `unreviewed`; it must never describe them as human-reviewed.

## Inventory and grouping

There are exactly 60 tasks from 30 independent synthetic base groups. Each group has two variants.
Ten groups (20 tasks) are `dev`; twenty groups (40 tasks) are `test`. A group never crosses splits.
The two variants share a template, job family and fixed reader/matcher fixtures; changing a title
or URL alone was not treated as a new independent scenario. Statistical inference uses groups as
the independent unit. This provides only 30 invented base groups, not 60 real-world scenarios.

## Frozen inputs

All candidate metadata, page bodies, candidate faults, profiles and job keys are in `catalog.json`.
Every page comes from the immutable SourceArchive under `source-archive/model-value-v1-c3`.
`snapshot_by_url[url]` gives the exact snapshot JSON path and `job_key_by_url[url]` gives the stable
logical job key. Faults such as `timeout`, `removed`, and `error` are consumed sequentially. The
same `load_case_pages` helper returns the same `BrowserPage` bytes to every planning strategy.
Reader scoring maps a delivered page through `job_key_by_url`; matcher inputs use the same archived
job text and deterministic `JobMatcher` implementation. Neither path may search or fetch.

## Pre-registered metrics and denominator

The Planner primary metric is the clean target completion rate on target-seeking cases:
`completed_tasks / planned_target_cases`, where completion requires the labeled valid job count,
zero constraint violations, zero unauthorized writes, and zero duplicate writes. Candidate
shortage, correct refusal and multi-job review are reported through the separate
`expected_outcome_satisfied` numerator and denominator; they are never added to the completion
numerator. The Reader reports correct delivered job keys, precision, coverage, review rate, and
error rate with all attempted pages in the denominator. Efficiency (steps, elapsed time, model
calls and observed tokens) is secondary. Missing usage is not zero.

Paired uncertainty uses a fixed-seed group bootstrap with 2,000 resamples. Repeated rounds of one
task are not independent groups. Do not turn a wide interval into a claim that strategies are equal.

## Default decision (pre-registered)

If any strategy has unauthorized writes, duplicate writes, or constraint violations, fix the runtime
before choosing between strategies. If model strategies do not show a supported quality gain and
are slower or more expensive, keep `rules` as the default. If `adaptive` has the same observed
quality as `always` while using fewer calls, `adaptive` may be selected, but it is not thereby shown
to beat `rules`. If a gain appears only in one validated edge category, route only that category to
the model. No performance result is pre-filled by this document.

## Human review status

`assistant_annotation.status = pending_human_review`, `human_reviewed = false`, and
`review_status = pending_human_review` in every label. `REVIEW.md` presents at least 12 stratified
cases with raw source facts and proposed decisions. Those proposals must be checked against the
source before formal use. The assistant does not claim the C3 human-review milestone is complete.

For exploratory or development runs, the manifest identifies these labels as
`unreviewed_assistant_proposal`; reports must use that label. A formal run may pass
`require_human_review=True` only after an external reviewer writes `HUMAN_REVIEW_RECEIPT.json`
with `schema_version: 1`, the matching `dataset_id`, the `manifest_sha256` computed from the
manifest returned by the loader, the exact sorted set of all 60 `reviewed_case_ids`, a non-empty
`reviewer`, an ISO-8601 `reviewed_at`, `status: complete`, and a substantive `attestation`. The
receipt is external review evidence; assistant-generated annotations cannot satisfy this gate.

## Known limitations

The data is invented and controlled; it cannot establish real postings, live availability, market
prevalence, or model quality on unseen public pages. Two variants of one template are correlated.
Several edge templates assert a source condition (for example empty model response or an injected
instruction) that is executed by the runner's fault policy, not proven by page text alone. The
dataset is frozen for offline comparison; proposed labels may change only in a new dataset version.
