# 本轮工作：Seed URL LLM 抽取器对比

## 目标

把 `--compare-llm-extractor` 从固定单页对比扩展为可接收多条 seed URL 的评测入口，为后续真实招聘页评测和 DeepSeek/Qwen 效果比较打基础。

## 改动内容

- 扩展 `run_llm_extractor_comparison`：
  - 支持读取命令行中的多个 `--seed-url`。
  - 每个 seed URL 作为独立 `EvaluationTask`，避免多个 URL 被合并成一个任务导致成功率失真。
  - 默认比较 `baseline` 和 `llm_demo`。
  - 如果传入 `--llm-extractor-provider deepseek|qwen`，额外加入真实 provider 结果。
- 新增 `evaluations/llm-extractor-comparison.md` 报告，汇总每个 extractor 的任务数、完成数、成功率、有效岗位数和失败原因。
- JSON 输出新增统一的 `extractors` 字段，同时保留旧的 `baseline` 和 `llm_demo` 字段，避免破坏已有调用。
- 更新 README、项目展示材料和 MVP 验证记录。

## 验证记录

新增测试先失败，失败原因是旧实现忽略传入 seed URL，只输出固定 `baseline: 0/1` 和 `llm-demo: 1/1`。

实现后执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_compare_llm_extractor_accepts_seed_urls_and_writes_report -q
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py::test_cli_compare_llm_extractor_can_include_provider_result -q
.\.venv\Scripts\python.exe -m pytest tests\test_scaffold.py tests\test_evaluation.py tests\test_llm_extractor.py -q
```

CLI smoke：

```powershell
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --seed-url "https://example.com/jobs/unstructured-ai-agent-intern" --seed-url "https://example.com/jobs/ai-engineering-intern" --json-output evaluations\seed-comparison.json
```

输出结果：

```text
LLM extractor comparison
baseline: 1/2
llm-demo: 2/2
Comparison report written to: evaluations/llm-extractor-comparison.md
Comparison JSON written to: evaluations\seed-comparison.json
```

## 结果解释

现在可以用同一个命令解释“为什么需要 LLM 抽取边界”：同一批 seed URL 中，规则抽取只完成部分页面，而 deterministic LLM demo 可以恢复低结构化 JD。后续加入 DeepSeek/Qwen provider 后，可以用同样的输出格式比较真实模型收益。

## 当前限制

- 当前 smoke 使用内置 demo URL，还不是外部真实招聘站点。
- 报告只统计任务级成功率，没有字段级完整度评分。
- provider 对比测试使用 fake provider，真实 DeepSeek/Qwen 批量评测仍需要单独执行并记录成本与失败原因。

## 下一步

- 增加一组真实招聘站点 seed URL，并把结果写入 work log。
- 为 comparison report 增加字段完整度评分，例如 title/company/location/requirements/responsibilities 覆盖率。
- 在 Dashboard 中展示 extractor/provider/model 维度的对比结果。
