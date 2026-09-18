# Work Log - 2026-09-18 Real Job Benchmark V1

## Goal

Freeze the 2026-09-09 showcase into a repeatable benchmark, then separate live URL
reachability from extraction and ranking on archived content.

## Dataset

- `catalog.json`: 40 cases, 14 ATS/source families, 32 companies.
- 37 cases have archived public JD content in `snapshots.jsonl`.
- 3 cases preserve source failures instead of deleting them.
- `ground-truth.json`: manually reviewed title/company/location/role/relevance/rank.
- Required skills remain `null` because the archived report does not provide an
  independent skill annotation.

## Runs

Command:

```powershell
.\.venv\Scripts\python.exe scripts\run_real_job_benchmark.py `
  --offline --providers baseline,snapshot-baseline --runs 3 `
  --recommendation-k 6
```

Results after three rounds on each path:

- Direct URL baseline: 0/40 source successes in every round; the latest round reached
  14 pages and classified failures as `page_drift=20`, `source_unavailable=6`, and
  `verification_filtered=14`.
- Frozen snapshot baseline: 37/37 successful extractions in every round.
- Snapshot title accuracy: 100% (`37/37` labeled cases).
- Snapshot company accuracy: 92.5% (`37/40` cases with company labels).
- Snapshot location accuracy: 100% (`37/37` labeled cases).
- Snapshot role-family accuracy: 64.86% (`24/37` labeled cases).
- Snapshot relevance accuracy: 81.08% (`30/37` labeled cases).
- Snapshot recommendation: P@6 66.67%, R@6 66.67%, NDCG@6 74.09%.

## Interpretation

The direct URL result is evidence that the original showcase depended on discovery
and source-specific content adapters. A saved detail URL is not sufficient to
reproduce live acquisition.

The snapshot result measures parser and ranking behavior on frozen public content.
It does not measure current site availability, all-web coverage, true field accuracy
on arbitrary live pages, or real-resume recommendation accuracy.

## Artifacts

- `evaluations/real-job-benchmark-v1/summary.json`
- `evaluations/real-job-benchmark-v1/summary.md`
- `evaluations/real-job-benchmark-v1/summary.html`
- `evaluations/real-job-benchmark-v1/runs/` (ignored raw run directories)
