# Real Planner Benchmark Design

## Objective

Build a reproducible benchmark that compares the deterministic policy, DeepSeek, and Qwen as semantic planners inside the same Hybrid Decision Agent runtime. The benchmark must measure planner behavior without changing the deterministic policy's ownership of authorization, retries, recovery, budgets, or termination.

## Scope

This iteration covers planner comparison only. It does not add live-site snapshots, human approval checkpoints, a hosted UI, model training, or fine-tuning. Those remain separate follow-up projects so planner quality is not confused with browser or extraction quality.

## Considered Approaches

### A. Extend the precomputed deterministic benchmark

Add provider labels to the existing synthetic result objects without executing the runtime. This is cheap and stable, but it cannot prove that a real LLM produced valid state-dependent decisions.

### B. Execute the same controlled runtime scenarios with each planner

Run replayable in-process pages and failure conditions through the actual LangGraph runtime, once per selected planner. This isolates planner quality while exercising authorization and fallback in production code. This is the selected approach.

### C. Compare planners only on live recruiting pages

Run each planner against current URLs and a real browser. This is realistic, but page drift, anti-bot behavior, extraction errors, and planner errors become confounded. It will be a later external-validity check, not the primary benchmark.

## Architecture

The new `agent_planner_benchmark.py` module owns four boundaries:

1. A versioned catalog of controlled runtime scenarios.
2. A runner that builds a fresh workflow and Hybrid runtime for every provider-case pair.
3. Typed provider and matrix summaries.
4. JSON and Markdown artifact rendering.

The existing `OpenAiCompatibleAgentPlanner` will collect aggregate call telemetry from provider responses: successful and failed calls, latency, prompt tokens, completion tokens, and total tokens. It will never store API keys, prompts, response bodies, resume text, or page bodies in benchmark artifacts.

The CLI will expose a dedicated benchmark mode rather than overloading the extraction-provider benchmark:

```text
web-task-agent --agent-planner-benchmark \
  --agent-planner-benchmark-providers deterministic,deepseek,qwen \
  --agent-planner-benchmark-output-dir docs/results/planner-benchmark
```

`deterministic` means no LLM planner is injected. DeepSeek and Qwen use the existing OpenAI-compatible provider configuration and environment-variable loading.

## Scenario Catalog

The first benchmark version will use five controlled scenarios:

1. `seed-happy-path`: one valid seeded JD reaches the target.
2. `search-happy-path`: no seed URL; the Agent must search, open, extract, and verify.
3. `open-recovery`: the first URL times out until its retry budget is exhausted, then the policy moves to a valid URL.
4. `verifier-recovery`: the first page is rejected and the policy moves to a second valid URL.
5. `budget-exhaustion`: the step budget prevents target completion but the loop terminates cleanly.

Every provider receives identical user state, page content, retry behavior, and step budget. Failure recovery remains policy-controlled, so the benchmark measures whether the planner can make authorized semantic decisions during normal states and whether invalid output falls back safely.

## Metrics

Each provider row will report:

- executed, skipped, or failed status;
- model name and benchmark version;
- task completion and loop termination rates;
- tool success and recovery success rates;
- planner calls, invalid decisions, and fallback rate;
- average and maximum consumed steps;
- end-to-end runtime and planner-only latency;
- prompt, completion, and total token counts when returned by the provider;
- per-case terminal reason and error detail.

Token counts are cost evidence, not a currency estimate. The report will not hard-code vendor prices because pricing changes independently of the repository.

## Missing Provider Behavior

A selected provider with a missing key is recorded as `skipped` with a non-secret reason. One unavailable provider does not erase results from available providers. The CLI exits non-zero only when no provider executes or an internal benchmark error prevents artifact generation.

Provider exceptions are recorded at the provider-case boundary. Runtime-level malformed or unauthorized decisions remain normal benchmark observations because the Hybrid runtime converts them into deterministic fallback.

## Artifacts And Honesty Boundaries

The default output directory contains:

- `planner-benchmark.json`: complete machine-readable matrix and per-case results;
- `planner-benchmark.md`: Chinese-first comparison, failure analysis, and interview wording.

The report will explicitly state that controlled fixtures measure orchestration and planner decision quality, not live-site extraction generalization. Results from a real API run are committed only after a secret scan confirms that no key, authorization header, prompt body, resume text, or raw model response is present.

## Testing

Tests will follow TDD and cover:

- provider-list parsing and validation;
- planner telemetry on successful and failed transport calls;
- identical case execution across injected planners;
- aggregation of completion, termination, fallback, latency, and tokens;
- missing-key skip behavior;
- JSON and Markdown scope labels;
- CLI routing and exit status without real network calls.

The final gate is Ruff plus the complete pytest suite. Real DeepSeek and Qwen calls are a separate smoke/benchmark command after deterministic tests pass.

## Acceptance Criteria

1. One command compares deterministic, DeepSeek, and Qwen over the same five scenarios.
2. JSON and Markdown artifacts distinguish executed, skipped, and failed providers.
3. The output reports completion, termination, invalid/fallback, steps, latency, and available token usage.
4. The benchmark cannot weaken URL allowlists, retry limits, failure recovery, or terminal policy control.
5. Automated tests do not require API keys or network access.
6. A real-provider run is accepted only when artifacts are generated, semantic metrics are populated, the process exits successfully, and the secret scan is clean.
