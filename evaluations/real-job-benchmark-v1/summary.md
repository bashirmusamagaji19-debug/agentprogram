# Real Job Benchmark

- Dataset: `real-job-benchmark-v1`
- Runs: 6
- Providers: baseline, snapshot-baseline

## Metric Summary

| Metric | Mean | Min | Max |
|---|---:|---:|---:|
| classification_accuracy | 0.4054 | 0.0000 | 0.8108 |
| company_accuracy | 0.4625 | 0.0000 | 0.9250 |
| location_accuracy | 0.5000 | 0.0000 | 1.0000 |
| ndcg_at_k | 0.3705 | 0.0000 | 0.7409 |
| precision_at_k | 0.3333 | 0.0000 | 0.6667 |
| recall_at_k | 0.3333 | 0.0000 | 0.6667 |
| role_family_accuracy | 0.3243 | 0.0000 | 0.6486 |
| title_accuracy | 0.5000 | 0.0000 | 1.0000 |

## Provider Summary

| Provider | Runs | Title | Company | Relevance | P@K | R@K | NDCG@K |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| snapshot-baseline | 3 | 1.0000 | 0.9250 | 0.8108 | 0.6667 | 0.6667 | 0.7409 |
