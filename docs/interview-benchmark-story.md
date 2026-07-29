# Hybrid Decision Agent 面试讲述

## 一句话

这是一个“LLM 做语义选择、确定性策略管安全”的 Web Task Agent：它会根据工具观察动态规划、恢复和终止，并用版本化 benchmark 证明行为边界。

## 60 秒讲法

我把原先固定顺序的 LangGraph 工作流升级成了 Hybrid Decision Agent。系统有搜索、打开页面、文本和视觉抽取、验证、匹配、保存、结束 8 个类型化工具，每次工具执行都会返回统一的 `ToolObservation`。可选 DeepSeek/Qwen 规划器负责语义选择，但代码策略控制动作白名单、步数预算、URL 重试和终止，所以 LLM 输出无效时不会失控，而是自动 fallback。比如页面连续失败会换候选 URL，文本置信度低或 verifier 拒绝会转视觉抽取。最后我做了 10 个确定性故障场景 benchmark，结果是 8/10 达到业务目标、10/10 正常终止、工具成功率 88.46%，同时把完成率和字段准确率分开统计。

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
- 设计“LLM 语义规划 + 确定性安全策略”架构，接入 DeepSeek/Qwen OpenAI-compatible planner，以代码约束动作白名单、步数预算、URL 重试和 fallback，并实现 URL 换链与 verifier 拒绝后的视觉恢复。
- 构建 10 场景确定性 Agent benchmark，覆盖无效规划、工具失败、恢复和终止边界；fixture 结果为 8/10 达成目标、10/10 正常终止、88.46% 工具调用成功，并输出版本化 JSON/Markdown 证据。

## 诚实边界

- 确定性 benchmark 使用合成 fixture，证明控制流可靠性，不证明真实招聘网站泛化。
- DeepSeek/Qwen planner 是可选增强；无 API key 时策略模式仍完整可用。
- 当前 CI 已配置 Python 3.11 全量测试和 70% 覆盖率门禁；本机 Windows 沙箱会因 `tmp_path` ACL 阻止 84 个文件系统测试，因此最终全量结果以 GitHub Actions 为准。
- 不需要云服务器、GPU、训练或微调。真实 provider 复跑只需要 API key；真实站点若出现 CAPTCHA 或地区限制才需要人工处理。
