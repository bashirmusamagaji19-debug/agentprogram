# Hybrid Decision Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed LangGraph demonstration path with a bounded hybrid decision Agent that selects typed tools, recovers from failures, discovers real job links, and emits credible Agent-specific evaluation evidence.

**Architecture:** Keep the existing sequential workflow as the regression baseline. Add a separate decision-observation runtime whose deterministic policy owns safety and termination while an optional OpenAI-compatible planner handles semantic choices. Existing browser, extractor, verifier, matcher, repository, reporter, and visual provider objects are wrapped as tools instead of rewritten.

**Tech Stack:** Python 3.11, Pydantic 2, LangGraph, browser-use, pytest, Ruff, GitHub Actions, optional DeepSeek/Qwen OpenAI-compatible API.

---

## File Map

- Create `agent_models.py`: action, decision, observation, budget, metrics, and state contracts.
- Create `agent_policy.py`: deterministic next-action and safety policy.
- Create `agent_tools.py`: async tool protocol, registry, and adapters.
- Create `agent_runtime.py`: bounded decision-observation loop and LangGraph graph.
- Create `agent_planner.py`: deterministic and optional OpenAI-compatible planners.
- Create `search_discovery.py`: parse, normalize, rank, and filter job links.
- Create `agent_evaluation.py`: scenario metrics and benchmark rendering.
- Modify `workflow.py`, `browser.py`, `cli.py`, `reporter.py`, and `dashboard.py`.
- Create focused test modules, CI, Ruff configuration, and versioned evidence under `docs/results/`.

### Task 1: Agent Contracts

**Files:**
- Create: `src/web_task_agent/agent_models.py`
- Test: `tests/test_agent_models.py`

- [ ] **Step 1: Write failing contract tests**

Test that an unknown action raises `ValidationError`, decision reasons cannot be blank, confidence is limited to `[0, 1]`, failed observations require an error category, and `AgentBudget.consume()` never produces negative remaining steps.

- [ ] **Step 2: Verify RED**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_models.py -q -p no:cacheprovider`

Expected: collection fails because `web_task_agent.agent_models` does not exist.

- [ ] **Step 3: Implement exact contracts**

Implement `AgentAction(str, Enum)` with `search_jobs`, `open_page`, `extract_text`, `extract_visual`, `verify_job`, `score_match`, `save_results`, and `finish`. Add immutable `AgentDecision`, validated `ToolObservation`, `AgentBudget`, `AgentMetrics`, and `DecisionAgentState` Pydantic models.

- [ ] **Step 4: Verify GREEN and commit**

Run the focused test, then commit with `feat: add hybrid agent state contracts`.

### Task 2: Deterministic Safety And Recovery Policy

**Files:**
- Create: `src/web_task_agent/agent_policy.py`
- Test: `tests/test_agent_policy.py`

- [ ] **Step 1: Write failing policy tests**

Create these named cases with explicit fixtures and assertions:

- `test_policy_stops_when_budget_is_exhausted` constructs a state with zero remaining steps and asserts `finish` plus terminal reason `budget_exhausted`.
- `test_policy_opens_next_candidate_after_recoverable_failure` provides two candidates, marks the first as failed twice, and asserts `open_page` targets the second URL.
- `test_policy_uses_visual_after_low_confidence_text_extraction` sets text confidence to `0.2`, enables visual extraction, and asserts `extract_visual`.
- `test_policy_finishes_when_target_count_is_reached` supplies one verified job for a target count of one and asserts `finish`.
- `test_policy_never_retries_a_url_more_than_twice` sets the URL retry counter to two and asserts the decision target differs from the failed URL.

Each test also asserts a non-empty decision reason and `source="policy"`.

- [ ] **Step 2: Verify RED**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_policy.py -q -p no:cacheprovider`

- [ ] **Step 3: Implement ordered rules**

Use this priority: target reached, budget exhausted, search needed, unopened candidate, text extraction, visual fallback, verification, matching, persistence, finish. Recoverable page failures retry once; after two attempts the policy must select the next URL.

- [ ] **Step 4: Verify GREEN and commit**

Commit with `feat: add deterministic agent recovery policy`.

### Task 3: Typed Tool Registry

**Files:**
- Create: `src/web_task_agent/agent_tools.py`
- Test: `tests/test_agent_tools.py`

- [ ] **Step 1: Write failing async tests**

Test registry execution, unknown-tool rejection, latency recording, exception conversion, page opening, text extraction, verification, and finish. Every execution must return `ToolObservation`.

- [ ] **Step 2: Verify RED**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_tools.py -q -p no:cacheprovider`

- [ ] **Step 3: Implement protocol and adapters**

Define `AgentTool(Protocol)` with `name` and `async execute(state, arguments)`. Implement `AgentToolRegistry` plus adapters around the existing browser, extractor, verifier, matcher, repository, and visual extractor. External exceptions become typed observations rather than escaping.

- [ ] **Step 4: Verify GREEN and commit**

Commit with `feat: expose workflow capabilities as agent tools`.

### Task 4: Bounded Runtime And Conditional LangGraph

**Files:**
- Create: `src/web_task_agent/agent_runtime.py`
- Modify: `src/web_task_agent/workflow.py`
- Test: `tests/test_agent_runtime.py`
- Modify: `tests/test_workflow.py`

- [ ] **Step 1: Write failing runtime scenarios**

Use fake planners and tools to assert:
- open failure selects the next URL;
- invalid planner output uses policy fallback;
- low-confidence text extraction routes to visual;
- target completion stops early;
- step exhaustion terminates;
- graph nodes are `initialize`, `decide`, `execute_tool`, `observe`, `guard`, and `finish`.

- [ ] **Step 2: Verify RED**

Run both focused test files with `-p no:cacheprovider`.

- [ ] **Step 3: Implement the loop and graph**

`HybridAgentRuntime.run()` validates a decision, executes a tool, records observation and metrics, consumes one step, applies policy overrides, and terminates explicitly. Conditional edges route `guard -> decide` or `guard -> finish`.

- [ ] **Step 4: Preserve the baseline**

Add `run_with_hybrid_agent()`; do not change `run()`. Keep the previous linear LangGraph entry point compatible during migration.

- [ ] **Step 5: Verify GREEN and commit**

Commit with `feat: add bounded conditional agent runtime`.

### Task 5: Real Search Result Discovery

**Files:**
- Create: `src/web_task_agent/search_discovery.py`
- Modify: `src/web_task_agent/browser.py`
- Test: `tests/test_search_discovery.py`
- Modify: `tests/test_browser.py`

- [ ] **Step 1: Write parser fixtures and failing tests**

Cover Google `/url?q=` redirects, direct Greenhouse/Lever links, duplicates, tracking parameters, unsupported schemes, Google navigation links, and obvious non-job pages.

- [ ] **Step 2: Verify RED**

Run both focused test files.

- [ ] **Step 3: Implement structured parsing**

Use `html.parser.HTMLParser` and `urllib.parse`. Normalize URLs, remove tracking parameters, reject unsupported schemes, and rank explicit job hosts/path signals without parsing full HTML through regular expressions.

- [ ] **Step 4: Wire search metadata**

Attach discovered links to `BrowserPage.metadata["candidate_urls"]`. The `search_jobs` tool enqueues those URLs and never sends the search page to the JD extractor.

- [ ] **Step 5: Verify GREEN and commit**

Commit with `feat: discover job links from search results`.

### Task 6: Hybrid LLM Planner

**Files:**
- Create: `src/web_task_agent/agent_planner.py`
- Test: `tests/test_agent_planner.py`

- [ ] **Step 1: Write fake-transport tests**

Cover valid JSON, fenced JSON, unknown actions, malformed JSON, provider timeout, and missing API key. Every invalid or unavailable response must return the deterministic fallback and increment metrics.

- [ ] **Step 2: Verify RED**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_planner.py -q -p no:cacheprovider`

- [ ] **Step 3: Implement compact structured planning**

Reuse existing OpenAI-compatible provider configuration. Send only goal, candidate summaries, last observation, retry counters, remaining budget, and allowed actions. Validate the response as `AgentDecision`; never include secrets or unbounded page text.

- [ ] **Step 4: Verify GREEN and commit**

Commit with `feat: add structured hybrid agent planner`.

### Task 7: CLI And Visible Decision Trace

**Files:**
- Create: `src/web_task_agent/agent_cli.py`
- Modify: `src/web_task_agent/cli.py`
- Modify: `src/web_task_agent/reporter.py`
- Modify: `src/web_task_agent/dashboard.py`
- Test: `tests/test_agent_cli.py`
- Modify: `tests/test_reporter.py`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing interface tests**

Require `--hybrid-agent`, `--agent-max-steps`, `--agent-planner-provider`, and `--agent-planner-model`. Require JSON metadata, Markdown, and HTML to show action, source, reason, outcome, latency, recovery, and remaining budget.

- [ ] **Step 2: Verify RED**

Run the focused CLI and rendering tests.

- [ ] **Step 3: Implement focused construction and rendering**

Move hybrid runtime construction to `agent_cli.py`. Render all presentation formats from the same serialized trace and escape HTML values.

- [ ] **Step 4: Verify GREEN and commit**

Commit with `feat: expose hybrid agent decisions in demo artifacts`.

### Task 8: Agent Evaluation And Versioned Evidence

**Files:**
- Create: `src/web_task_agent/agent_evaluation.py`
- Test: `tests/test_agent_evaluation.py`
- Create: `docs/results/hybrid-agent-benchmark.json`
- Create: `docs/results/hybrid-agent-benchmark.md`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing metric tests**

Assert task completion, tool success, recovery success, invalid action, fallback, average steps, termination, latency, and title/company/location accuracy from deterministic traces.

- [ ] **Step 2: Verify RED, implement, and verify GREEN**

Field accuracy compares outputs against explicit ground truth. Pipeline completion remains a separate metric.

- [ ] **Step 3: Generate sanitized evidence**

Run the ten approved deterministic scenarios without API keys. Commit only sanitized summaries under `docs/results/`; runtime directories remain ignored.

- [ ] **Step 4: Commit**

Commit with `feat: add agent recovery benchmark evidence`.

### Task 9: CI And Quality Gates

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add Ruff and coverage configuration**

Add `ruff>=0.12`. Configure Python 3.11, line length 100, and rules `E`, `F`, `I`, `B`, and `UP`. Measure coverage and set a threshold no lower than 70%.

- [ ] **Step 2: Add CI**

CI installs `.[dev]`, runs Ruff, runs deterministic pytest, and uploads coverage XML. Real-site and provider tests are opt-in.

- [ ] **Step 3: Run local checks**

Run:
- `..\..\.venv\Scripts\python.exe -m ruff check src tests`
- `..\..\.venv\Scripts\python.exe -m pytest -p no:cacheprovider`

If the Windows ACL still blocks `tmp_path`, record the exact result and use GitHub Actions as the authoritative full-suite run.

- [ ] **Step 4: Commit**

Commit with `ci: verify hybrid agent quality gates`.

### Task 10: Documentation And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/project-story.md`
- Modify: `docs/interview-benchmark-story.md`
- Modify: `docs/mvp-verification.md`
- Create: `docs/work-log/2026-07-29-hybrid-decision-agent.md`

- [ ] **Step 1: Align claims**

Document architecture, tools, two recovery examples, benchmark version/date, exact metric definitions, limitations, and one stable demo. Remove stale test counts and label the historic 8-page result as completion rather than accuracy.

- [ ] **Step 2: Add resume and interview wording**

Provide three resume bullets plus 60-second and 3-minute explanations focused on structured decisions, conditional routing, bounded recovery, fallback, and evaluation.

- [ ] **Step 3: Run final verification**

Run Ruff, all focused Agent tests, full pytest, `web-task-agent --doctor`, deterministic benchmark, and demo. Inspect JSON and HTML for secrets and malformed traces.

- [ ] **Step 4: Verify repository state and commit**

Require a clean worktree and review `git diff master...HEAD --stat`. Commit with `docs: present hybrid decision agent evidence`.

## Human And External Checkpoints

- No cloud server, GPU, training, or fine-tuning is required.
- Human action is needed only for local API key presence, GitHub authentication, real benchmark ground-truth review, and CAPTCHA/region checks.
- Deterministic implementation and CI must not depend on external API access.
- Do not push until the user approves final benchmark wording and public evidence.
