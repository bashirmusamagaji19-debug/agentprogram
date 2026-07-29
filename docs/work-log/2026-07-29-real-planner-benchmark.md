# 2026-07-29 真实 Planner 对照评测

## 目标

把 Hybrid Decision Agent 从“支持 DeepSeek/Qwen Planner”推进到“能用相同任务量化不同 Planner 行为”。本轮不训练模型，不接云 GPU，也不把实时网页漂移混入主要评测变量。

## 设计

- 固定 5 个受控 runtime 场景：seed happy path、search happy path、open recovery、verifier recovery、budget exhaustion。
- 每个 provider 使用相同用户目标、候选页面、失败条件和步数预算。
- LLM 只负责正常状态下的结构化动作选择；确定性策略继续控制 URL 白名单、失败恢复、重试、预算和终止。
- 记录动作/来源枚举和聚合数字，不保存 prompt、response、API key、简历或页面正文。

## 实现

- `agent_planner.py`：新增数字型 `PlannerTelemetry`，记录调用成功/失败、延迟和 token usage。
- `agent_planner_benchmark.py`：新增场景 catalog、provider matrix、聚合模型和 JSON/Markdown renderer。
- `cli.py`：新增 `--agent-planner-benchmark`、provider 列表与输出目录参数。
- 场景数据库与内部报告使用临时目录，运行结束后自动清理，公开目录只保留 JSON/Markdown。

## 评测中发现并修复的问题

`verifier-recovery` 第一次运行会在切换到第二个 URL 后继续验证第一页的旧岗位，最终耗尽预算。根因是 policy 判断“是否存在任何 extracted job”，没有判断“当前页面 URL 是否已有 extracted job”。

修复后，policy 会在打开新页面后重新执行 `extract_text`。新增回归测试 `test_policy_extracts_new_current_page_instead_of_reusing_previous_job`，五场景中的 verifier recovery 从 budget exhaustion 恢复为 target reached。

## 最终真实 API 结果

Benchmark version：`hybrid-agent-planner-controlled-v1`

| Planner | 模型 | 完成 | 终止 | Planner calls | Invalid / fallback | 平均步数 | Planner latency | Prompt / Completion / Total Token |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| deterministic | deterministic-policy-v1 | 4/5 | 5/5 | 0 | 0 / 0 | 3.8 | 0 ms | 0 / 0 / 0 |
| DeepSeek | deepseek-v4-flash | 4/5 | 5/5 | 15 | 5 / 5 | 3.4 | 50.37 s | 2941 / 2577 / 5518 |
| Qwen | qwen-plus | 4/5 | 5/5 | 16 | 0 / 0 | 3.2 | 28.10 s | 4087 / 990 / 5077 |

`budget-exhaustion` 被故意设置为 1 步预算，因此三种路径的 4/5 目标完成不是模型失败；三种路径都是 5/5 正常终止。

## 行为差异

- deterministic 在 `open-recovery` 先对坏 URL 重试两次，再打开有效 URL，共消耗 5 步。
- Qwen 第一决策直接打开有效候选，`open-recovery` 只消耗 3 步，且 16 次 Planner 调用没有触发 fallback。
- DeepSeek 在 15 次调用中产生 5 次未授权决策。runtime 拒绝后使用 deterministic fallback，最终仍保持 4/5 完成、5/5 终止。
- 这批数据支持“LLM 可以减少无效探索，确定性策略负责安全兜底”的架构叙述，但样本只有 5 个受控场景，不能证明模型在真实网页上普遍更强。

## 公开证据

- `docs/results/planner-benchmark/planner-benchmark.json`
- `docs/results/planner-benchmark/planner-benchmark.md`

敏感字段扫描覆盖 `Authorization`、`Bearer`、key 环境变量名、`api_key`、`resume_text`、`messages` 和 response 字段，结果均无匹配。

## 复现命令

```powershell
.\.venv\Scripts\web-task-agent.exe --agent-planner-benchmark `
  --agent-planner-benchmark-providers deterministic,deepseek,qwen `
  --agent-planner-benchmark-output-dir docs/results/planner-benchmark
```

真实 DeepSeek/Qwen 运行需要本机环境变量；自动化测试不需要密钥或网络。无需云服务器或 GPU。

## 面试表达

> 我没有只展示“接入了两个模型”，而是把 deterministic、DeepSeek 和 Qwen 放入同一个五场景 Hybrid Agent runtime。Qwen 将平均步数从 3.8 降到 3.2，并在失败 URL 场景直接选择有效候选；DeepSeek 的未授权决策全部被代码策略拒绝并 fallback。这样既能量化模型带来的探索效率，也能证明安全和终止不是依赖 prompt 自觉。
