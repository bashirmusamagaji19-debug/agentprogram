# 本轮工作：Benchmark 结果解释层

## 完成了什么

- 新增 `benchmark_explainer.py`：确定性中文 insight 生成，不调 LLM。
- `BenchmarkInsight` 包含：一句话结论、provider 解读、失败原因说明、visual provider 价值、工程判断、60 秒/3 分钟面试讲法。
- `FAILURE_EXPLANATIONS` 覆盖全部 7 个实际 failure category（verification_filtered、browser_error、no_pages、no_extracted_jobs、http_timeout、http_error、empty_page）。
- 新增 `evaluations/benchmark-v2-explained.md` artifact。
- `--benchmark-explain` CLI 参数：一键生成 raw matrix + JSON + dashboard + 解释报告。
- Dashboard 在开启 explain 时展示短版解释摘要。
- 5 个单元测试 + 1 个 dashboard 测试 + 1 个 CLI smoke 测试。

## 你要理解什么

这一层不改变抽取器能力，也不引入新的 LLM 判断。它只基于已有的 `BenchmarkMatrixResult` 做确定性总结，所以结果可测试、可复跑、可审查。面试时可以把它理解为 raw benchmark 和人类叙事之间的翻译层。

## 你现在应该做什么

运行：

```powershell
.\.venv\Scripts\web-task-agent.exe --benchmark-v2 --benchmark-providers baseline,llm-demo,deepseek --benchmark-limit 8 --benchmark-dashboard --benchmark-explain
```

然后按顺序查看：
- `evaluations/benchmark-v2.json`
- `evaluations/benchmark-v2.md`
- `evaluations/benchmark-v2-explained.md`  ← 面试用这个
- `dashboards/benchmark-v2.html`
