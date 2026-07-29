# Hybrid Decision Agent Benchmark

- Version: `hybrid-agent-deterministic-v1`
- Date: `2026-07-29`
- Scope: synthetic deterministic scenarios
- Boundary: pipeline completion is not extraction accuracy.

## Aggregate Metrics

| Metric | Value |
|---|---:|
| Task completion rate | 80.00% |
| Loop termination rate | 100.00% |
| Tool success rate | 88.46% |
| Recovery success rate | 50.00% |
| Invalid action rate | 100.00% |
| Deterministic fallback rate | 100.00% |
| Average steps | 4.20 |
| Maximum steps | 6 |
| Title accuracy | 87.50% |
| Company accuracy | 100.00% |
| Location accuracy | 100.00% |
| Combined field accuracy | 95.83% |
| Total deterministic latency | 260.00 ms |
| Planner/provider calls | 2 |

Invalid-action and fallback rates use planner/provider calls as their denominator. This fixture set intentionally makes both provider calls invalid to verify fallback.

## Scenarios

| Case | Scenario | Completed | Terminated | Steps | Terminal reason | Actions |
|---|---|---|---|---:|---|---|
| `happy-path` | Deterministic happy path | true | true | 4 | `target_reached` | `search_jobs -> open_page -> extract_text -> verify_job -> finish` |
| `search-filtering` | Search results filter non-job links | true | true | 4 | `target_reached` | `search_jobs -> open_page -> extract_text -> verify_job -> finish` |
| `open-recovery` | First URL fails and second succeeds | true | true | 5 | `target_reached` | `search_jobs -> open_page -> open_page -> extract_text -> verify_job -> finish` |
| `visual-recovery` | Weak text routes to visual extraction | true | true | 5 | `target_reached` | `search_jobs -> open_page -> extract_text -> extract_visual -> verify_job -> finish` |
| `unsupported-tool` | Unsupported planner action uses policy fallback | true | true | 4 | `target_reached` | `search_jobs -> open_page -> extract_text -> verify_job -> finish` |
| `invalid-json` | Malformed planner JSON uses policy fallback | true | true | 4 | `target_reached` | `search_jobs -> open_page -> extract_text -> verify_job -> finish` |
| `verifier-recovery` | Verifier rejection recovers with visual evidence | true | true | 6 | `target_reached` | `search_jobs -> open_page -> extract_text -> verify_job -> extract_visual -> verify_job -> finish` |
| `all-candidates-fail` | All candidates fail and terminate cleanly | false | true | 3 | `no_action_available` | `search_jobs -> open_page -> open_page -> finish` |
| `budget-exhausted` | Step budget is exhausted | false | true | 3 | `budget_exhausted` | `search_jobs -> open_page -> extract_text -> finish` |
| `target-reached` | Target count stops the Agent early | true | true | 4 | `target_reached` | `search_jobs -> open_page -> extract_text -> verify_job -> finish` |

## Interpretation

This benchmark exercises orchestration and recovery with controlled fixtures. Field accuracy is computed only where explicit ground truth exists. It does not measure generalization to live websites or claim production extraction quality.
