# Hybrid Decision Agent Design

## 1. Goal

Upgrade the existing linear Web Task Agent into a hybrid decision Agent that demonstrates two interview-relevant capabilities:

1. Dynamic planning and failure recovery based on page type, extraction confidence, verifier results, and remaining execution budget.
2. Explicit tool selection through a typed registry, with every decision and observation recorded as evidence.

The target is a two-day implementation sprint. The result must remain reproducible without model access while supporting DeepSeek or Qwen as the semantic decision provider.

## 2. Scope

### In scope

- A structured `AgentDecision` contract with a bounded action set.
- A structured `ToolObservation` contract shared by all tools.
- A tool registry covering search, page opening, text extraction, visual extraction, verification, matching, persistence, and finish.
- A hybrid planner: LLM decisions for ambiguous choices, deterministic policy for hard constraints and fallback.
- A LangGraph runtime with conditional routing and bounded recovery loops.
- Real search-result link discovery before opening candidate job pages.
- Decision, tool, recovery, latency, and budget traces in JSON, Markdown, and the HTML Dashboard.
- Deterministic scenario tests and a small real-site smoke suite.
- Agent-specific evaluation metrics and versioned benchmark evidence.
- CI, linting, and documentation required to make the result credible on GitHub.

### Out of scope

- Model training, fine-tuning, or GPU infrastructure.
- Autonomous job application submission.
- Login, CAPTCHA bypass, or anti-bot evasion.
- Additional LLM or VLM providers.
- A complete rewrite of the existing CLI.
- Long-term memory, vector retrieval, or multi-user service deployment.
- A fully general-purpose browser Agent.

## 3. Architecture

The runtime follows a decision-observation loop rather than a fixed pipeline:

```text
User Goal
  -> Initialize State
  -> Decision Node
     -> Select Tool
     -> Validate Action and Budget
     -> Execute Tool
     -> Record Observation
     -> Apply Safety Policy
     -> Decision Node
  -> Finish
  -> Report / Dashboard / Evaluation
```

The LLM is responsible only for semantic choices such as candidate prioritization and recovery selection. Code remains authoritative for URL validation, allowed actions, step limits, retry limits, cost limits, and terminal conditions.

## 4. State And Contracts

### Agent action

The allowed action enum is:

```text
search_jobs
open_page
extract_text
extract_visual
verify_job
score_match
save_results
finish
```

`AgentDecision` contains:

- `action`: one allowed action.
- `reason`: concise explanation for the choice.
- `target`: optional URL or job identifier.
- `arguments`: validated tool arguments.
- `confidence`: value from 0 to 1.
- `source`: `llm`, `policy`, or `fallback`.

### Tool observation

`ToolObservation` contains:

- `tool_name`.
- `success`.
- `summary`.
- `payload` with JSON-compatible structured data.
- `error_category` and `error_message` when unsuccessful.
- `latency_ms`.
- `recoverable`.

### Runtime state

The Agent state extends the existing workflow state with:

- current goal and user profile.
- current URL and current page.
- candidate URL queue and visited URL set.
- extracted and verified jobs.
- last decision and last observation.
- complete decision and observation history.
- total step budget and remaining steps.
- per-tool and per-URL retry counters.
- provider call count and estimated cost.
- terminal status and terminal reason.

## 5. Components

### Decision models

`agent_decision.py` defines the action enum and Pydantic decision model. Invalid actions, missing targets, non-finite confidence, and invalid arguments are rejected before execution.

### Tool contracts and registry

`agent_tools.py` defines a common async tool protocol and a registry that maps actions to implementations. Tools wrap existing browser, extractor, verifier, matcher, and repository components rather than duplicating them.

The initial registry contains:

- `SearchJobsTool`
- `OpenPageTool`
- `ExtractTextTool`
- `ExtractVisualTool`
- `VerifyJobTool`
- `ScoreMatchTool`
- `SaveResultsTool`
- `FinishTool`

### Deterministic policy

`agent_policy.py` applies non-negotiable rules before and after LLM planning:

- Refuse unsupported tools and unsafe URL schemes.
- Stop when the step budget is exhausted.
- Stop when the requested number of verified jobs is reached.
- Retry a URL no more than twice.
- Prefer text extraction before visual extraction.
- Route low-confidence text extraction to visual extraction when available.
- Move to the next URL after an unrecoverable page failure.
- Finish with an explicit partial or failed status when no candidates remain.

The same policy supplies a deterministic next action whenever the LLM is disabled, unavailable, times out, or returns invalid output.

### LLM planner

`agent_planner.py` sends a compact state summary and the allowed action schema to an existing OpenAI-compatible provider. The response is validated as `AgentDecision`. It must not receive API keys, raw secrets, or unbounded page content.

The planner is used for:

- prioritizing candidate links from search results.
- deciding whether a weak extraction is worth visual fallback.
- deciding whether verifier rejection is recoverable.
- selecting which verified job should be matched or saved next.

### Runtime and LangGraph routing

`agent_runtime.py` owns the execution loop and step budget. `workflow.py` exposes it through LangGraph nodes:

```text
initialize -> decide -> execute_tool -> observe -> guard
                ^                         |
                |-------------------------|
guard -> finish when a terminal condition is reached
```

Conditional edges route according to the validated action and terminal state. The existing sequential workflow remains available as a baseline and migration fallback.

### Search discovery

The real browser path must not treat a Google result page as a job posting. Search discovery parses candidate links, removes duplicates and unsupported schemes, filters obvious non-job pages, and enqueues candidate URLs for `open_page`.

The first implementation only needs to support deterministic fixtures plus a small public-site smoke set. It does not attempt CAPTCHA bypass or broad search-engine scraping.

### Trace and presentation

Every loop iteration records:

- step number.
- selected action and source.
- decision reason and confidence.
- tool input summary.
- observation success, latency, and error category.
- budget before and after the step.
- recovery outcome when the previous step failed.

JSON is the source of truth. Markdown and Dashboard views render the same trace without separate business logic.

## 6. Failure Recovery

Required recovery paths are:

1. Page-open failure -> retry once when recoverable -> choose next URL when still failing.
2. Text extraction below confidence threshold -> visual extraction when configured -> otherwise next URL.
3. Verifier rejection caused by missing fields -> retry through the alternative extractor once.
4. Invalid LLM JSON or unsupported action -> record invalid action -> deterministic fallback.
5. Provider timeout or missing API key -> deterministic fallback without failing the task.
6. Empty candidate queue -> finish with partial or failed status.
7. Step budget exhausted -> finish with `budget_exhausted`, never loop indefinitely.

All recoveries must be visible in the execution trace and evaluation output.

## 7. Evaluation

### Deterministic scenario suite

The benchmark contains at least these scenarios:

1. Normal text job page.
2. Search results containing job and non-job links.
3. First URL fails and the second succeeds.
4. Text extraction is weak and visual extraction succeeds.
5. LLM returns an unsupported tool.
6. LLM returns invalid JSON.
7. Verifier rejects the first extraction and accepts the recovered extraction.
8. All candidates fail and the Agent terminates cleanly.
9. Step budget is exhausted.
10. Target job count is reached and the Agent stops early.

### Metrics

The primary metrics are:

- task completion rate.
- tool success rate.
- recovery attempt count and recovery success rate.
- invalid action rate.
- deterministic fallback rate.
- average and maximum steps.
- loop termination rate.
- field-level accuracy for title, company, and location.
- end-to-end latency and provider call count.

Pipeline completion must not be described as extraction accuracy. Real-site results must identify the sample set, date, provider, and metric definition.

## 8. Testing Strategy

- Unit tests cover decision validation, URL validation, budgets, retry counters, and fallback rules.
- Contract tests require every tool to return `ToolObservation`, including errors.
- Runtime tests use fake tools and fake planners to assert exact action sequences.
- LangGraph tests assert conditional routes and termination conditions.
- Integration tests exercise the existing extractor, verifier, matcher, repository, and report boundaries.
- Real-site smoke tests are opt-in and do not block deterministic CI.
- CI runs tests, Ruff checks, and coverage reporting on Python 3.11.

## 9. Two-Day Delivery Boundary

### Day 1

- Implement state, decision, observation, tool registry, deterministic policy, and runtime budget.
- Implement search, open, text extraction, verification, and finish tools.
- Add conditional LangGraph routing.
- Complete the core deterministic recovery tests.

### Day 2

- Add LLM planning and deterministic fallback.
- Add visual extraction recovery when the sibling package is available.
- Add Agent metrics, report trace, Dashboard trace, and versioned benchmark output.
- Add CI and Ruff.
- Align README, project story, benchmark wording, resume bullets, and demo script.
- Publish the locally completed commits after verification.

If time becomes constrained, visual fallback is optional. Search discovery, bounded routing, invalid-action fallback, recovery metrics, CI, and public evidence are mandatory.

## 10. External Resources And Human Actions

No cloud server, GPU, training job, or model fine-tuning is required.

Human actions are limited to:

- configuring `DEEPSEEK_API_KEY` or `DASHSCOPE_API_KEY` locally when real planner evaluation is desired.
- completing GitHub authentication or device approval when required for push.
- reviewing the ground-truth labels for 8 to 12 real benchmark pages.
- completing a browser CAPTCHA or regional access check if a public site requires it.
- approving final public resume wording and any personal information.

The deterministic Agent path, test suite, and CI must work without external API keys.

## 11. Acceptance Criteria

- The LangGraph path contains conditional routing and at least two tested recovery loops.
- The Agent selects tools through a validated registry and records a reason for every choice.
- Invalid or unavailable LLM decisions fall back deterministically.
- The runtime always terminates within its configured budget.
- Real search discovery opens candidate job URLs instead of treating the search page as a job.
- A fixed benchmark reports Agent-specific metrics and field-level quality separately.
- GitHub contains a versioned benchmark summary and visible demo evidence.
- CI passes on the published commit.
- README and resume claims use the same current metrics and clearly state limitations.
