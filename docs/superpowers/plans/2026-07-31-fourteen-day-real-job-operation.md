# Fourteen-Day Real Job Operation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a versioned 14-day dataset of at least 100 unique real Chinese AI internship postings from at least 20 public official recruitment sources, plus human ground truth and an auditable final evaluation.

**Architecture:** Treat live web access as an operational collection process, not CI. Validate each source manually before catalog inclusion, run one bounded daily capture, freeze raw inputs, annotate a stratified sample, and evaluate static HTML, rendered browser, and VLM fallback on identical snapshots.

**Tech Stack:** Existing `web-task-agent` real-job commands, JSON/JSONL artifacts, PowerShell, Git, manual annotation

---

**Prerequisites:** Complete and verify `2026-07-31-real-job-data-foundation.md` before day zero. Complete Tasks 1-5 of `2026-07-31-visual-source-recovery.md` before running the VLM row of the same-snapshot matrix; the first static and rendered collection days do not depend on a GPU.

### Task 1: Build the First Verified 20-Company Catalog

**Files:**
- Modify: `data/real-jobs/source-catalog.json`
- Create: `docs/results/real-jobs/source-verification.md`

- [ ] **Step 1: Select companies before URLs**

Use this fixed company set for the first verification pass: 字节跳动、阿里巴巴、腾讯、百度、美团、京东、华为、小米、快手、哔哩哔哩、滴滴、网易、蚂蚁集团、携程、联想、OPPO、vivo、商汤科技、科大讯飞、智谱 AI。

- [ ] **Step 2: Verify each official recruitment entry manually**

For each company, locate the recruitment entry from the company's official domain or official campus-recruitment link. Record the checked URL, final redirected URL, check timestamp, public-access result, and evidence that the page is owned by the company. Do not use a search-result URL or third-party repost as the catalog entry.

- [ ] **Step 3: Apply the admission rule**

Add a source only if all are true:

```text
HTTPS URL
publicly reachable without login
official company or official ATS ownership is verifiable
page exposes job listings or a stable path to listings
check timestamp and result are recorded
```

If one of the fixed 20 fails, replace it with another Chinese technology company and document the rejected source and reason. The final catalog must contain at least 20 admitted sources, not merely 20 attempted companies.

- [ ] **Step 4: Validate catalog structure**

```powershell
.\.venv\Scripts\python.exe -c "from web_task_agent.real_jobs.catalog import load_source_catalog; s=load_source_catalog('data/real-jobs/source-catalog.json'); assert len(s) >= 20; print(len(s))"
```

Expected: prints an integer at least 20.

- [ ] **Step 5: Commit the verified catalog**

```powershell
git add data/real-jobs/source-catalog.json docs/results/real-jobs/source-verification.md
git commit -m "data: add verified official recruitment sources"
```

### Task 2: Run a Day-Zero Smoke Before Starting the Clock

**Files:**
- Create: `data/real-jobs/runs/day-00/` (ignored)
- Modify: `docs/results/real-jobs/source-verification.md`

- [ ] **Step 1: Run five sources with no VLM**

```powershell
.\.venv\Scripts\web-task-agent.exe --real-job-run `
  --real-job-catalog data\real-jobs\source-catalog.json `
  --real-job-output-dir data\real-jobs\runs\day-00 `
  --real-job-max-sources 5
```

Expected: exit 0 only if at least one source executes successfully and `run-summary.json` exists.

- [ ] **Step 2: Inspect every failure record**

```powershell
Get-Content data\real-jobs\runs\day-00\failures.jsonl
```

Expected: every line has one defined failure code and a nonempty source ID and URL.

- [ ] **Step 3: Replay the frozen snapshots**

Run the offline replay command produced by the foundation implementation against `day-00`. Expected: it makes no network calls and produces identical snapshot IDs and content hashes.

- [ ] **Step 4: Fix only blockers to daily evidence**

Allowed changes are incorrect failure classification, missing artifact fields, resource leaks, and non-idempotent writes. Site-specific feature expansion and model tuning are out of scope.

- [ ] **Step 5: Rerun day zero until the audit contract passes**

Expected: all successes have evidence hashes; all failures have failure codes; duplicate writes after replay are zero.

### Task 3: Execute the 14 Daily Runs

**Files:**
- Create: `data/real-jobs/runs/YYYY-MM-DD/` (ignored)
- Create: `docs/results/real-jobs/daily-log.md`

- [ ] **Step 1: Run the fixed daily command once per calendar day**

```powershell
$day = Get-Date -Format 'yyyy-MM-dd'
.\.venv\Scripts\web-task-agent.exe --real-job-run `
  --real-job-catalog data\real-jobs\source-catalog.json `
  --real-job-output-dir "data\real-jobs\runs\$day" `
  --real-job-max-sources 100
```

Expected: one immutable directory per date. Do not rerun into the same directory; put corrective replays under `<date>-replay-<commit>`.

- [ ] **Step 2: Record daily counts immediately**

Append date, commit, sources attempted, sources successful, unique active jobs, newly discovered jobs, removed jobs, failures by code, static/rendered/visual counts, elapsed time, token count, and estimated cost to `daily-log.md`.

- [ ] **Step 3: Preserve external failures**

Do not remove a source or sample just to improve the day's success rate. Catalog changes require a separate commit and a reason in `source-verification.md`.

- [ ] **Step 4: Check the run directory contract**

Each directory must contain:

```text
run-summary.json
snapshots.jsonl
jobs.jsonl
failures.jsonl
```

The summary must identify code commit, catalog hash, started/finished timestamps, and exit status.

- [ ] **Step 5: Repeat until 14 distinct dates are present**

```powershell
(Get-ChildItem data\real-jobs\runs -Directory | Where-Object Name -Match '^\d{4}-\d{2}-\d{2}$').Count
```

Expected: `14` or greater.

### Task 4: Build Human Ground Truth

**Files:**
- Modify: `data/real-jobs/ground-truth.jsonl`
- Create: `docs/results/real-jobs/annotation-guide.md`

- [ ] **Step 1: Freeze the annotation sample list**

Select at least 100 snapshots stratified across companies, dates, success/failure paths, core/adjacent roles, dynamic pages, and suspected duplicates. Save the selected snapshot IDs before annotation begins.

- [ ] **Step 2: Apply the same annotation guide to every sample**

For each sample record validity, company, exact visible job title, location, core/adjacent/irrelevant class, responsibilities evidence, requirements evidence, removed status, and duplicate target. Empty or ambiguous evidence remains empty and is not inferred.

- [ ] **Step 3: Validate annotations mechanically**

```powershell
.\.venv\Scripts\python.exe -c "from web_task_agent.real_jobs.artifacts import JsonlStore; from web_task_agent.real_jobs.models import GroundTruthRecord; p='data/real-jobs/ground-truth.jsonl'; a=JsonlStore(p, GroundTruthRecord).read_all(); assert len(a)>=100; assert len({x.snapshot_id for x in a})==len(a); print(len(a))"
```

Expected: prints at least 100 with no duplicate snapshot IDs.

- [ ] **Step 4: Review 20 annotations twice**

Re-annotate a fixed 20-record subset without viewing the first result. Report agreement separately for valid-job decision, job class, company, title, and location. Resolve disagreements by evidence, not by changing model output.

- [ ] **Step 5: Commit redacted ground truth**

```powershell
git add data/real-jobs/ground-truth.jsonl docs/results/real-jobs/annotation-guide.md
git commit -m "data: add real job ground truth"
```

### Task 5: Run the Same-Snapshot Provider Matrix

**Files:**
- Create: `docs/results/real-jobs/final-evaluation.json`
- Create: `docs/results/real-jobs/final-evaluation.md`
- Create: `docs/results/real-jobs/per-sample-results.jsonl`

- [ ] **Step 1: Freeze the dataset manifest**

Record dataset version, snapshot IDs, snapshot hashes, catalog hash, annotation hash, date range, and code commits from both repositories. The three provider paths must read this same manifest.

- [ ] **Step 2: Run static HTML baseline**

Run frozen-snapshot evaluation with provider name `static_html`. It must make zero network calls and write per-sample predictions and failures.

- [ ] **Step 3: Run rendered-browser recovery**

Evaluate the saved rendered artifacts for the same snapshot IDs with provider name `rendered_browser`. Missing rendered evidence is an explicit failure, not a skipped sample.

- [ ] **Step 4: Run VLM fallback only on eligible failed samples**

Use `visual-web-agent` on the identical failed-snapshot subset. Record model identity, adapter identity, image hash, latency, error, and output. Do not change the subset after seeing results.

- [ ] **Step 5: Generate the matrix report**

```powershell
.\.venv\Scripts\web-task-agent.exe --real-job-evaluate `
  --real-job-ground-truth data\real-jobs\ground-truth.jsonl `
  --real-job-output-dir docs\results\real-jobs
```

Expected: report includes sample count, date range, provider matrix, field metrics, evidence support, dedup metrics, recovery, cost, latency, and every gate failure.

- [ ] **Step 6: Enforce honest acceptance**

If a threshold fails, keep the final artifact and state the failure. Do not replace samples, merge older metrics, or adjust ground truth after viewing provider scores.

### Task 6: Run the Real User Relevance Loop

**Files:**
- Create: `data/real-jobs/user-feedback.local.jsonl` (ignored)
- Create: `docs/results/real-jobs/user-relevance-summary.md`

- [ ] **Step 1: Generate one Top 20 list per day from available active jobs**

Use the user's actual target: domestic AI application, Agent, AI engineering, and adjacent backend/algorithm/platform internships in Beijing, Shanghai, Shenzhen, Hangzhou, or remote.

- [ ] **Step 2: Record one decision per recommendation**

Allowed decisions are `prepare_to_apply`, `not_considering`, `false_recommendation`, `applied`, and `expired`. Preserve the model score and reason separately from human feedback.

- [ ] **Step 3: Calculate Top-20 relevance without training on the labels**

For the first 14-day report, compute the percentage marked core or adjacent and worth considering. Do not change ranking weights during the measurement window.

- [ ] **Step 4: Redact and aggregate the feedback report**

Publish counts and categorized reasons, not resume body, personal contact information, or application credentials.

### Task 7: Publish the Final Evidence Package

**Files:**
- Modify: `README.md`
- Modify: `docs/interview-benchmark-story.md`
- Create: `docs/work-log/2026-08-14-real-job-validation.md`

- [ ] **Step 1: Run final checks**

```powershell
.\.venv\Scripts\web-task-agent.exe --release-check
.\.venv\Scripts\python.exe -m pytest tests\real_jobs -q
git diff --check
```

Expected: all checks pass.

- [ ] **Step 2: Verify artifact counts and date range**

Require at least 20 admitted sources, 100 unique real jobs, 100 annotations, and 14 daily runs. Report actual counts even when larger.

- [ ] **Step 3: Update project claims from final artifacts only**

README and interview docs must cite dataset version, provider, sample size, dates, and metric definition next to every number. Preserve failed acceptance gates, site failures, and zero-delta VLM results.

- [ ] **Step 4: Record reproducibility commands and known limitations**

Include frozen replay, evaluation, release check, source restrictions, data retention, and the fact that live sites can drift.

- [ ] **Step 5: Commit the evidence package**

```powershell
git add README.md docs/interview-benchmark-story.md docs/results/real-jobs docs/work-log/2026-08-14-real-job-validation.md
git commit -m "docs: publish real job validation evidence"
```

### Task 8: Expand Sources Only After Official-Source Acceptance

**Files:**
- Create: `docs/superpowers/specs/2026-08-15-reposted-source-design.md`

- [ ] **Step 1: Check the official-source gate**

Proceed only when the final official-source artifact contains 20 sources, 100 jobs, 14 daily runs, complete failure classifications, and a versioned ground-truth evaluation.

- [ ] **Step 2: Design the reposted-source phase separately**

Specify source provenance, original-link validation, duplicate handling, and metric separation for 牛客. Do not mix reposted-source results into the official-source baseline.

- [ ] **Step 3: Defer restricted platforms**

BOSS and 拉勾 require a later design covering authentication, terms, privacy, rate limits, and stop conditions. Do not implement login automation or CAPTCHA bypass under this plan.
