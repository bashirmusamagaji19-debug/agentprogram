# Real Job Benchmark V1

This dataset freezes 40 cases from `run-843296ac` on 2026-09-09.

## Composition

- 37 cases are job postings that produced at least one valid job in the archived run.
- 3 cases preserve source failures instead of removing them: two State Grid URLs and
  one BYD URL.
- The catalog spans more than four ATS/source families and more than eight companies.
- `content_sha256` hashes the normalized evidence block archived in the showcase
  report. It is not a hash of a full HTML snapshot.
- `snapshots.jsonl` contains the 37 archived public job-description blocks used by
  `snapshot-baseline`. The three source failures intentionally have no snapshot.

## Annotation Rules

- Title, company, location, role family, relevance, and optional rank were manually
  reviewed from the archived report.
- Relevance is judged against the synthetic profile used by the showcase:
  Python, LangGraph, RAG, Agent, and limited research-level ML/CV/RL evidence.
- `recommended_top_k` ranks only cases considered worth applying to under that profile.
- Required skills are intentionally `null`: the archived report does not provide a
  reliable independent skill annotation. Missing labels are excluded from denominators
  and are never replaced with zero.
- Failed-source cases retain null labels and use `source_unavailable`.
- The synthetic evaluation profile is fixed across live and snapshot runs:
  Python, LLM, RAG, LangGraph, and Agent.

## Repeatable Runs

Run three live rounds and three frozen-snapshot rounds:

```powershell
.\.venv\Scripts\python.exe scripts\run_real_job_benchmark.py `
  --offline --providers baseline,snapshot-baseline --runs 3 `
  --recommendation-k 6
```

The live row measures current URL reachability and page drift. The snapshot row
measures extraction and ranking on the frozen archive without claiming live success.

## Known Limits

- The sample comes from one six-minute run and is not a random sample of the web.
- It measures source reachability, archived field agreement, and recommendation
  ordering. It does not prove field accuracy for all live pages, full-web coverage,
  or long-term source stability.
- Live pages can drift after collection time. Re-run and version a new dataset instead
  of silently editing this one when the source content changes.
