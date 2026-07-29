# 2026-07-29 Hybrid Decision Agent 工作日志

## 本轮目标

在不破坏 sequential 与旧 LangGraph 基线的前提下，把固定工作流升级为能动态选工具、根据失败恢复、受预算约束且可评测的 Hybrid Decision Agent。项目目标是增强 Agent 应用开发的面试可讲性，不进行模型训练。

## 架构决策

- LLM planner 负责语义选择，不负责安全控制。
- `DeterministicAgentPolicy` 负责动作白名单、目标数量、步数预算、URL 重试、fallback 和终止。
- 既有 browser/extractor/verifier/matcher/repository 通过 typed tool adapter 复用，不重写业务模块。
- 新 runtime 与旧 `run()` 并存，便于回归和面试对比。

```text
initialize -> decide -> execute_tool -> observe -> guard
                 ^                            |
                 +----------------------------+
guard -> finish when terminal
```

## 完成内容

1. 新增 `AgentAction`、`AgentDecision`、`ToolObservation`、`AgentBudget`、`AgentMetrics` 和 `DecisionAgentState` 契约。
2. 新增 8 个类型化工具：`search_jobs`、`open_page`、`extract_text`、`extract_visual`、`verify_job`、`score_match`、`save_results`、`finish`。
3. 新增 LangGraph 条件循环、确定性策略和可选 DeepSeek/Qwen OpenAI-compatible planner。
4. 搜索页解析真实岗位链接，支持 Google redirect 解码、去重、tracking 参数清理与非岗位链接过滤。
5. 新增 URL 失败重试/换链、低置信度 text-to-visual、verifier rejection 恢复和无效 planner fallback。
6. CLI 增加 `--hybrid-agent`、`--agent-max-steps`、`--agent-planner-provider` 和 `--agent-planner-model`，JSON/Markdown/HTML 展示完整决策轨迹。
7. 新增 10 场景 `hybrid-agent-deterministic-v1` 与 `docs/results/` 公开证据。
8. 新增 Python 3.11 GitHub Actions、Ruff 聚焦门禁和 70% coverage 门禁。
9. 新增 planner state authorization：外部 URL 不在候选白名单时拒绝执行；工具失败后的恢复与终止由 policy 强制接管。

## 关键调试事件

发现 verifier 拒绝文本结果后，旧策略没有专门恢复分支，可能反复打开并验证同一页面直至预算耗尽。先添加两个回归测试复现：有视觉能力时应转 `extract_visual`，无视觉能力时应选择下一个候选；随后在 `_recover_from_failure()` 增加最小修复。相关策略、runtime 和工具测试共 20 项通过后独立提交。

## 评测口径与结果

公开证据：`docs/results/hybrid-agent-benchmark.json` 与 `.md`。

| 指标 | 结果 | 说明 |
|---|---:|---|
| 业务目标完成率 | 80% | 8/10 以 `target_reached` 结束 |
| 循环终止率 | 100% | 10/10 均明确终止 |
| 工具成功率 | 88.46% | 46/52 工具调用成功 |
| 恢复成功率 | 50% | 包含候选全部失败和预算耗尽场景 |
| 综合字段准确率 | 95.83% | 仅显式 ground truth fixture，不代表真实站点 |

两个 planner 调用都故意返回非法结果，所以 invalid-action rate 和 fallback rate 都是 100%。这证明 fallback 路径被覆盖，不代表真实 provider 的错误率。

## 验证记录

- Hybrid Agent 聚焦测试：`44 passed`。
- Ruff 聚焦检查：`All checks passed`。
- 稳定 demo：`completed / target_reached`，动作链为 `search_jobs -> open_page -> extract_text -> verify_job -> finish`。
- 全量本地 pytest：`169 passed, 84 errors`。84 个错误均来自 Windows 沙箱拒绝 pytest `tmp_path` 创建，错误为 `PermissionError: [WinError 5]`；没有观察到业务断言失败。
- 全量本地 coverage 在 84 个文件系统测试未执行时为 66.68%；CI 在 Linux 运行全部测试并执行 70% 门禁。

## 面试要点

- 旧 LangGraph 是固定状态工作流，新 runtime 才体现根据观察动态决策与恢复。
- LLM 不等于 Agent 控制器；生产可靠性来自白名单、结构化契约、预算、重试和可解释终止。
- 完成率、终止率、恢复率和字段准确率分开报告，避免把“跑完流程”包装成“抽取准确”。
- 公开 JSON/Markdown/HTML trace 让每个 action、reason、observation、latency 和 fallback 都可复盘。

## 人工与外部操作

- 本轮不需要云服务器、GPU、训练或微调。
- 只有运行真实 DeepSeek/Qwen planner 时需要用户配置 API key。
- 推送分支或创建 PR 前需要 GitHub 登录；当前按约定未推送。
- 真实站点 ground truth、CAPTCHA 或地区访问限制需要人工确认。
