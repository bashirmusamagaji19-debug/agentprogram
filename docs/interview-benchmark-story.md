# Hybrid Decision Agent 面试讲述

最新验收（2026-10-25）：完整 release-check 六项通过，全量 pytest/coverage 阶段通过，覆盖率 **87.99%**。Windows 日志编码的小修复另经 17 项定向测试验证。已完成14条人工复核及 v4 的720个正式配对单元，维持 rules 默认；真实来源10条任务均无交付。报告见 [正式对照结果](results/formal-model-value-v4-20261025/DECISION.md)。后续说明见 [人工任务说明](HUMAN_REVIEW.md)。

历史核查（2026-10-25 修复前）：`956 passed`、`87.75%` coverage，另有 1 failed、1 skipped；修复后的验收结果见本轮收尾记录，不能将此历史核查写成全绿。

## Portfolio 复现

```powershell
.\.venv\Scripts\web-task-agent.exe --portfolio-demo --portfolio-demo-output-dir portfolio-artifacts
.\.venv\Scripts\web-task-agent.exe --release-check
```

两条命令不读取 provider API key，不依赖公网、云服务器或 GPU。前者生成 deterministic Hybrid Agent 和 HITL approve/reject/replay 证据；后者运行 CI 等价的 focused Ruff、pytest/coverage、wheel、doctor、strict msgpack 和 Git 检查。

## 一句话

这是一个“LLM 做语义选择、确定性策略管安全”的 Web Task Agent：它会根据工具观察动态规划、恢复和终止，并用版本化 benchmark 证明行为边界。

## 60 秒讲法

网页运行台与 CLI 调用共享 Hybrid 服务，完成岗位发现、关键词与工作地联合过滤、抽取和可解释匹配。能力封装成 8 个类型化工具，LLM 在白名单内做语义选择，确定性 policy 控制预算、恢复和终止。保存前用 LangGraph `interrupt` 做人工审批，checkpoint 按 `thread_id` 跨进程恢复，业务 SQLite 用 `approval_id` receipt 防止重复保存。网页已接入 Hybrid/HITL；拒绝路径不产生写入，失败恢复可以沿 trace 复盘。受控评测和跨进程验收证明控制边界，真实网站泛化仍需外部验证。

## 3 分钟讲法

第一，为什么要改。旧版本虽然用了 LangGraph，但本质是固定六节点链，只能证明“有状态工作流”，不能充分证明 Agent 会根据环境变化做决策。因此我保留旧路径作为回归基线，新增独立 Hybrid runtime，避免破坏已有功能。

第二，怎么设计。状态层用 Pydantic 定义 `AgentDecision`、`ToolObservation`、`AgentBudget`、指标和完整状态。能力层把已有 browser、extractor、verifier、matcher、repository 包装为 8 个工具。运行时是 `initialize -> decide -> execute_tool -> observe -> guard` 条件循环。LLM 只接收目标、候选摘要、上一步观察、重试计数和剩余预算，不接收简历正文或完整页面；确定性策略对安全和终止拥有最终控制权。

第三，怎么恢复。打开页面失败时先检查错误是否可恢复和单 URL 重试次数，超限后换下一个候选；文本抽取低置信度或 verifier 拒绝时，如果视觉工具可用就转 `extract_visual`，否则跳过当前 URL；规划器返回未知动作、非法 JSON 或超时时，Pydantic 校验失败并切回 policy 决策。这样恢复不是 prompt 里的一句话，而是有状态、有预算、可测试的控制逻辑。

第四，怎么证明。CLI 的 JSON、Markdown 和 HTML 都展示 action、source、reason、target、observation、latency、budget 和 terminal reason。`hybrid-agent-deterministic-v1` 固定 10 个合成场景，覆盖正常路径、搜索链接过滤、URL 失败、text-to-visual、无效工具、非法 JSON、verifier 拒绝、全部候选失败、预算耗尽和提前完成。结果为 80% 业务目标完成率、100% 循环终止率和 88.46% 工具成功率。这个 benchmark 主要证明编排和恢复，不宣称真实网站泛化能力。

## 两个恢复案例

| 触发条件 | 决策链 | 工程价值 |
|---|---|---|
| 第一个 URL 打开失败并耗尽重试 | `open_page(fail) -> open_page(next URL)` | 防止卡死在单一候选，重试次数有上限 |
| 文本结果被 verifier 拒绝 | `extract_text -> verify_job(fail) -> extract_visual` | 用另一种证据恢复，而不是重复验证同一结果 |

## 指标口径

- 业务目标完成率：`terminal_reason == target_reached` 的场景比例。
- 循环终止率：所有不再处于 `running` 的场景比例，包括预期的预算耗尽和候选耗尽。
- 工具成功率：成功 `ToolObservation` 数 / 工具调用总数。
- 恢复成功率：失败后下一次恢复调用成功数 / 恢复尝试数。
- 字段准确率：只在有显式 ground truth 的 fixture 上比较 title/company/location。
- invalid-action 与 fallback rate：分母是 planner/provider 调用；当前两个调用都故意注入非法输出，因此两项均为 100%，用于验证降级路径，不代表线上错误率。

Pipeline completion 不是 extraction accuracy。历史 2026-06-22 的 8 页真实站点结果是 DeepSeek/Qwen 各 7/8 任务完成率，不应写成 88% 抽取准确率；网页内容变化后也需要重跑。

## 简历三条

- 基于 Python、LangGraph 与 Pydantic 构建 Hybrid Decision Agent，将岗位搜索、页面访问、文本/视觉抽取、验证、匹配和持久化封装为 8 个类型化工具，实现条件决策循环与可审计 execution trace。
- 设计“LLM 语义规划 + 确定性安全策略”架构，并在 `save_results` 前实现 LangGraph Human-in-the-loop：稳定 `thread_id` 支持跨进程恢复，`approval_id` SQLite receipt 保证 replay 不重复写入，reject 以 `human_denied` 结束。
- 构建版本化 deterministic fixture 评测与离线复现入口：10/10 Agent 循环终止、HITL 3/3 pause、reject/duplicate effects 0；项目当前（2026-10-05）`632 passed`、`87.35%` coverage，另有 1 项未配置 PostgreSQL 的集成测试跳过，并提供 Streamlit 运行台、`--portfolio-demo` 与 `--release-check`。

## 当前验收补充（2026-10-05）

完整产物与演示步骤见 [收尾证据](results/portfolio-acceptance-2026-10-05/README.md)。北京、上海、北京/上海三轮小规模真实搜索各返回 2 条满足条件的岗位，部分失效候选保留 404 诊断。多工作地岗位可以同时满足不同城市输入；结果数量不等于覆盖率。

三条受控页面上，规则完成任务 2/3，Qwen 增强路径 3/3，这是任务完成率而非字段准确率。16 条既有合成标注样本的规则匹配为 15/16，纯 Qwen 为 13/16，混合方案为 15/16。可以讲模型补对语义边界样本、同时有乐观打分问题；不能声称 LLM 整体优于规则。

2026-10-23 的 v2 开发 pilot 在 6 条任务、3 个独立组上分离 Planner 与 Reader：Planner 三策略均为 6/6，但 adaptive 使用 4 次模型调用、always 使用 24 次；Reader 的规则与 adaptive 为 6/6，always 为 4/6 并产生 14 次调用。开发证据支持规则默认，不支持把全量模型设为生产默认。正式 test 尚未运行，因为标签仍待人工复核；详细结果见 `results/model-value-v2-20261023/DECISION.md`。

本轮独立进程验收覆盖审批通过保存 1 条、拒绝保存 0 条、重复恢复不增加写入；本地 File adapter 的四项产物重启恢复哈希一致。云端 PostgreSQL/S3 仍无真实部署验收。

## 诚实边界

- 确定性 benchmark 使用合成 fixture，证明控制流可靠性，不证明真实招聘网站泛化。
- DeepSeek/Qwen planner 是可选增强；无 API key 时策略模式仍完整可用。
- Streamlit、CLI 与验收脚本复用同一 Hybrid service；HITL 的批准、拒绝和重复恢复已由独立进程验收。视觉恢复仍使用确定性 adapter，不能描述为已验证真实 Qwen-VL 视觉准确率。
- 当前 CI 配置 Python 3.11 focused Ruff、全量 pytest 和 70% 覆盖率门禁；本地 `--release-check` 复用相同范围并额外验证 wheel、doctor、strict HITL 和 Git whitespace。
- 不需要云服务器、GPU、训练或微调。真实 provider 复跑只需要 API key；真实站点若出现 CAPTCHA 或地区限制才需要人工处理。
