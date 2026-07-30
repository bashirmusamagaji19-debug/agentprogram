# Hybrid Agent HITL Checkpoint Evaluation

- Version: `hybrid-agent-hitl-v1`
- Scope: deterministic checkpoint and persistence scenarios
- Pause rate: 1.00
- Rejected-path effects: 0
- Duplicate effects: 0

This deterministic checkpoint benchmark uses controlled fixtures and does not measure real-site extraction quality.

| Case | Paused | Approved | Rejected | Replayed | Saved effects | Terminal reason |
|---|---|---|---|---|---:|---|
| approve | true | true | false | false | 1 | target_reached |
| reject | true | false | true | false | 0 | human_denied |
| replay | true | true | false | true | 1 | target_reached |
