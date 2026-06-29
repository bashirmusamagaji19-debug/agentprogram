# 本轮工作：Real Site Benchmark V2

## 完成了什么

- 新增 `benchmark.py`：`BenchmarkCase`（样本目录，含公司/ATS/岗位族/期望信号）、`BenchmarkProviderResult`（单 provider 聚合结果）、`BenchmarkMatrixResult`（完整矩阵）、`run_benchmark_matrix()`（可注入 runner）。
- 新增 `parse_benchmark_providers()`：逗号分隔 provider 列表解析、去重、合法性校验。
- 新增 Markdown / JSON / HTML 三格式 benchmark artifact。
- CLI 新增 `--benchmark-v2`、`--benchmark-providers`、`--benchmark-limit`、`--benchmark-dashboard` 参数。
- `--print-demo-script` 新增 benchmark 命令。
- 测试：8 个 catalog/metadata 测试、3 个 matrix/render 测试、1 个 runner 测试、1 个 CLI smoke 测试、1 个 dashboard HTML 测试。

## 架构

```
benchmark.py:
  BenchmarkCase → .to_evaluation_task() → EvaluationTask
  run_benchmark_matrix(cases, providers, run_provider) → BenchmarkMatrixResult
  render_benchmark_markdown() / write_benchmark_artifacts()

cli.py:
  run_cli_benchmark_v2(args, providers) → 注入 run_provider → 每个 provider:
    baseline  → EvaluationRunner(HttpPageLoader)
    llm-demo  → + DemoLlmFieldExtractor
    deepseek/qwen → + build_cli_llm_field_extractor
    qwen-vl   → + build_configured_visual_extractor (try/finally close)
```

## URL 重复说明

`build_real_site_benchmark_v2_cases()` 的 8 个 URL 与 `evaluation.build_real_site_sample_tasks()` 重复。Benchmark catalog 新增了 company/ATS/role_family/expected_signal 元数据，长期方向是让 evaluation 的 task builder 从 catalog 派生。

## 验证命令

```powershell
# 全量测试
.\.venv\Scripts\python.exe -m pytest

# 基准命令（无需 API key）
.\.venv\Scripts\web-task-agent.exe --benchmark-v2 --benchmark-providers baseline,llm-demo --benchmark-limit 2 --benchmark-dashboard

# 基准命令（需要 DEEPSEEK_API_KEY）
.\.venv\Scripts\web-task-agent.exe --benchmark-v2 --benchmark-providers baseline,llm-demo,deepseek --benchmark-limit 8 --benchmark-dashboard

# 产出物
evaluations/benchmark-v2.json   (机器可读)
evaluations/benchmark-v2.md     (Markdown 报告)
dashboards/benchmark-v2.html    (HTML 摘要)
```

## 面试讲述要点

- "我把真实站点评测升级成了 provider matrix——8 个样本 × N 个 provider，每个样本有公司、ATS、岗位族元数据。"
- "不是一次性的 `--compare-llm-extractor`，而是可重复跑的基准测试，有 JSON/Markdown/HTML 三种产出物。"
- "benchmark 不假设 URL 永远稳定——失败以 failure_counts 记录下来，变成数据而不是隐藏的假设。"
