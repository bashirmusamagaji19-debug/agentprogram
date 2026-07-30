# 2026-07-30 简历项目收尾工作日志

## 目标与停止标准

本轮不再增加 Agent 功能，而是把现有 Hybrid Decision Agent + durable HITL 工程能力整理成招聘方能快速理解、面试现场能离线复现、所有数字能追溯的实习简历项目。

达到以下条件后停止开发：README 一分钟可扫描；无 API key 能运行 portfolio demo；本地 release-check 与 CI 门禁一致；简历数字来自实际测试或版本化 artifact；敏感字段扫描通过；Git 提交小而可回滚。

## 方案选择

采用“证据驱动的 portfolio 收尾”，不建设 Web 审批后台、多 Agent 或云端 checkpoint。原因是当前 Agent 深度已经足以支撑面试，继续加功能会稀释主线；更重要的是形成稳定演示、可靠门禁和诚实指标。

设计与计划：

- `docs/superpowers/specs/2026-07-30-resume-portfolio-finish-design.md`
- `docs/superpowers/plans/2026-07-30-resume-portfolio-finish.md`

## 完成内容

### 离线 portfolio demo

新增 `--portfolio-demo` 和 `--portfolio-demo-output-dir`。命令不读取 provider API key，不访问公网，依次运行：

1. environment doctor；
2. deterministic Hybrid Agent，输出可审计 JSON、Markdown 和 HTML；
3. HITL approve/reject/replay benchmark，输出版本化 JSON 和 Markdown。

真实 smoke 在约 7 秒内完成，Hybrid Agent 以 `target_reached` 结束；HITL 结果为 `pause_rate=1.00`、拒绝副作用 `0`、重复副作用 `0`。

### CI 等价 release-check

新增 `agent_release_check.py` 和 `--release-check`，执行六个阶段并保留每阶段状态：

- focused Ruff（与 `.github/workflows/ci.yml` 相同范围）；
- pytest + coverage 70% 门禁；
- wheel build；
- doctor；
- `LANGGRAPH_STRICT_MSGPACK=true` HITL benchmark；
- `git diff --check`。

真实运行六阶段全部 `[PASS]`，耗时约 80 秒。检查器显式展开 Ruff 文件列表，避免 Python subprocess 与 PowerShell/Bash glob 语义不同。

### 招聘呈现

- README 顶部新增简历项目入口、一句话架构、准确指标和两条复现命令。
- `docs/interview-benchmark-story.md` 把 durable HITL、checkpoint/repository 双层职责和 exactly-once 可见副作用作为主要工程故事。
- 简历三条、60 秒讲法和诚实边界统一使用当前证据。
- 默认 `portfolio-artifacts/` 加入 `.gitignore`，演示不会污染工作树。

## TDD 与调试事件

### SQLite `:memory:` 跨连接陷阱

portfolio 测试首次运行以 `budget_exhausted` 结束。trace 显示前三步抽取、验证和匹配均成功，但 `save_results` 连续报 `OperationalError: no such table: save_receipts`。

根因是 Repository 每次操作打开新连接，而 SQLite `:memory:` 数据库按连接隔离：初始化连接创建的 schema 对保存连接不可见。最小修复是让 portfolio demo 使用输出目录中的文件型 `portfolio.sqlite`，而不是增加预算或隐藏保存失败。修复后同一测试以 `target_reached` 结束，并继续进入 HITL 阶段。

## 最终验证证据

| 检查 | 结果 |
|---|---|
| 全量 pytest | `308 passed` |
| 总覆盖率 | `91.17%`，门槛 70% |
| `agent_release_check.py` | 100% coverage |
| CI 等价 focused Ruff | PASS |
| wheel build | PASS |
| doctor | PASS |
| strict msgpack HITL | PASS |
| portfolio Hybrid Agent | `target_reached` |
| HITL fixture | 3/3 pause，reject effects 0，duplicate effects 0 |
| artifact 敏感字段扫描 | 无匹配 |

全仓 `ruff check .` 仍有 105 项历史风格债务，但 GitHub Actions 从未以全仓为门禁；本轮严格复用 CI 的 Agent 模块范围并通过。没有用忽略规则伪造全仓绿色，也没有无关格式化旧模块。

## 最终简历三条

- 基于 Python、LangGraph 与 Pydantic 构建 Hybrid Decision Agent，将岗位搜索、页面访问、文本/视觉抽取、验证、匹配和持久化封装为 8 个类型化工具，实现条件决策循环与可审计 execution trace。
- 设计“LLM 语义规划 + 确定性安全策略”架构，并在 `save_results` 前实现 durable Human-in-the-loop：稳定 `thread_id` 支持跨进程恢复，`approval_id` SQLite receipt 保证 replay 不重复写入，reject 以 `human_denied` 结束。
- 构建版本化 deterministic fixture 评测和离线复现入口：10/10 Agent 循环终止、HITL 3/3 pause、reject/duplicate effects 0；项目最终 `308 passed`、`91.17%` coverage。

## 人工与外部操作

- 本地 portfolio 路径不需要云服务器、GPU、训练、微调、API key 或人工数据标注。
- 只有重跑真实 DeepSeek/Qwen provider v2 才需要用户配置 API key；当前简历结论不依赖该步骤。
- push、创建 PR、合并或删除 worktree 仍需用户明确选择，本轮未自动执行。

## 提交

- `bc65aa8 feat: add offline portfolio demo entrypoint`
- `b20c384 feat: add CI-equivalent release check`
- 最终招聘文档、设计、计划和本日志使用独立 documentation commit。
