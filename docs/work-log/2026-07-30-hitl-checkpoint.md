# 2026-07-30 HITL Checkpoint 工作日志

## 本轮目标

在 Hybrid Decision Agent 的 `save_results` 外部副作用之前加入可持久化人工审批，使流程能够在程序退出后由另一个进程恢复，同时保证拒绝不写库、重复恢复不产生重复保存。重点是体现 Agent 应用开发中的状态编排、安全边界、故障恢复、审计和评测能力；本轮不涉及模型训练。

## 架构与幂等决策

- LangGraph 在 `prepare_approval` 后进入 `approval_gate`，通过 `interrupt` 暂停，暂停发生在 `save_results` 之前。
- `langgraph-checkpoint-sqlite` 保存工作流状态；恢复端必须提供稳定且相同的 `thread_id`，并用 `Command(resume=...)` 继续原执行。
- planner 只能提出工具动作，不能批准自己的副作用。批准和拒绝由 runtime 的确定性控制流处理。
- approve 继续执行原 `save_results`；reject 不调用保存工具，并以 `human_denied` 终止。
- checkpoint 负责“流程从哪里继续”，业务数据库 receipt 负责“保存是否已经发生”。`approval_id` 是 receipt 唯一键，岗位写入和 receipt 写入处于同一 SQLite 事务中。
- 这两层共同关闭“业务写入成功，但 checkpoint 尚未提交时进程崩溃”导致重复可见副作用的窗口。checkpoint 不是长期语义记忆，也不替代业务数据库。
- 审批 payload 和公开审计只包含审批 ID、线程 ID、动作、岗位数量、摘要、时间、结果和公开备注，不包含简历正文、页面正文、模型原始响应或 API key。

## 主要实现

- `agent_approval.py`：审批请求、决策、状态、审计事件和 HITL 运行结果契约。
- `agent_checkpoint.py`：异步 SQLite checkpointer 生命周期、路径校验和显式 msgpack 类型白名单。
- `agent_runtime.py`：暂停节点、恢复 API、稳定线程配置、批准/拒绝分支和错误语义。
- `storage.py` 与 `agent_tools.py`：事务型 receipt 和幂等保存。
- `agent_policy.py`：完成顺序修正为匹配、保存、再以 `target_reached` 结束。
- `cli.py`、`agent_cli.py` 与 `workflow.py`：HITL 启动/恢复参数、跨进程运行和脱敏 JSON/Markdown/HTML 审计。
- `agent_hitl_evaluation.py`：approve、reject、replay 三个确定性场景及版本化证据。
- `agent_planner_benchmark.py`：动作序列变更后升级为 `hybrid-agent-planner-controlled-v2`，历史 v1 证据保持不变。

## 提交记录

| Commit | 内容 |
|---|---|
| `b721e24` | HITL checkpoint 设计 |
| `c7f0ff9` | 分步实施计划 |
| `53b02c0` | 审批契约和依赖 |
| `de0f158` | 业务保存幂等 receipt |
| `ec0d033` | Windows 下显式关闭 repository SQLite 连接 |
| `85791d8` | 修正 Agent 完成顺序 |
| `33a1eb9` | 异步 SQLite checkpointer 生命周期 |
| `9de90f0` | LangGraph 暂停和恢复 runtime |
| `574f76b` | CLI 暂停和恢复入口 |
| `05b3d5e` | 脱敏审批审计证据 |
| `33d8757` | 版本化 HITL fixture 评测 |

最终文档提交将在本日志完成并通过发布检查后生成。

## 调试事件：Windows SQLite 文件占用

HITL benchmark 第一次在 pytest 临时目录清理时触发 `PermissionError: [WinError 32]`。根因不是 checkpoint 场景失败，而是 Python 的 `with sqlite3.Connection` 只管理事务提交/回滚，不会关闭连接；Windows 因此仍锁定临时数据库文件。

修复方式是为 `JobRepository` 增加显式关闭连接的 context manager，让成功和异常路径都执行 `close()`，并增加“运行后可删除数据库文件”的回归测试。修复单独放在 `ec0d033`，避免把平台生命周期问题混入幂等功能提交。

## 评测证据与边界

公开证据位于：

- `docs/results/hitl-checkpoint/hitl-checkpoint.json`
- `docs/results/hitl-checkpoint/hitl-checkpoint.md`

`hybrid-agent-hitl-v1` 最终结果：3/3 场景在保存前暂停，`pause_rate=1.00`；reject 保存副作用为 0；replay 重复副作用为 0；approve 和 replay 以 `target_reached` 结束，reject 以 `human_denied` 结束。

这些场景是受控的 deterministic fixtures，只验证审批、跨连接恢复、拒绝和幂等边界，不衡量真实招聘网站抽取质量，也不证明 LLM planner 泛化能力。动作序列改变后，provider benchmark 使用 v2 目录；本轮没有把历史 DeepSeek/Qwen v1 数字复制成新的 v2 结果。

## 验证记录

- 文档契约测试：`1 passed`。
- 全量覆盖率测试：`301 passed`，总覆盖率 `90.74%`，高于 70% 门槛。
- `LANGGRAPH_STRICT_MSGPACK=true` 下运行 HITL benchmark，三场景成功且未出现未注册类型警告。
- wheel 构建成功：`web_task_agent-0.1.0-py3-none-any.whl`，构建时大小 102792 bytes。
- `web-task-agent --doctor` 检查 Python、virtualenv、LangGraph、browser-use、Pydantic 和输出目录全部正常。
- HITL 新模块及其测试的定向 Ruff 检查为 `All checks passed!`。
- 全仓 `ruff check .` 仍报告 105 项既有风格债务，包括旧文件长行、import 顺序、`load_dotenv` 后 import 触发的 E402 和旧测试 unused import。该门禁当前不是绿色；本轮没有为追求数字而无关格式化整个仓库。
- `git diff --check origin/master` 通过，没有 whitespace error。

复现命令：

```powershell
..\..\.venv\Scripts\python.exe -m pytest
..\..\.venv\Scripts\python.exe -m pytest --cov=web_task_agent --cov-report=term-missing
..\..\.venv\Scripts\python.exe -m ruff check .
..\..\.venv\Scripts\python.exe -m pip wheel . --no-deps --wheel-dir dist
..\..\.venv\Scripts\web-task-agent.exe --doctor
$env:LANGGRAPH_STRICT_MSGPACK = 'true'
..\..\.venv\Scripts\web-task-agent.exe --hitl-benchmark --hitl-benchmark-output-dir docs/results/hitl-checkpoint
Remove-Item Env:LANGGRAPH_STRICT_MSGPACK
git diff --check origin/master...HEAD
git status --short
```

## 面试讲法

> 我没有把 Human-in-the-loop 做成一次普通的命令行确认，而是把它设计成 Agent 状态机里的持久化安全边界。LangGraph checkpoint 让进程退出后仍能按 `thread_id` 恢复，但 checkpoint 不能保证业务副作用 exactly-once，所以我又用 `approval_id` 在业务 SQLite 事务中写唯一 receipt。这样 approve 可以恢复原动作，reject 明确以 `human_denied` 结束，崩溃重放也不会重复保存。最后我用 approve、reject、replay 三类 fixture 分别验证暂停率、拒绝副作用和重复副作用，而不是把“流程跑完”误报成真实网页抽取准确率。

可以进一步追问的工程点：

- 为什么 planner 无权自批，审批必须在确定性 runtime 中处理。
- 为什么 checkpoint 和业务幂等是两个职责，单独一个不能形成完整保证。
- 为什么审批不消耗工具步数，避免人工等待污染 Agent budget 指标。
- 为什么要显式限制 msgpack 可反序列化类型，以及如何用 strict 模式验证未来兼容性。
- 为什么保留自动 `runtime.run()`，使旧流程、回归测试和 HITL 演示可以并存。

## 当前限制与人工操作

- 当前审批入口是 CLI，不包含 Web 审批后台、鉴权、角色权限、超时升级和多人审批策略。
- SQLite 适合单机演示和面试项目；多实例生产部署应换成共享 checkpoint backend，并设计分布式幂等与租约。
- benchmark 使用 demo fixture；真实站点仍会受到登录、CAPTCHA、反爬和页面结构变化影响。
- 本轮不需要云服务器、GPU、训练、微调或人工数据标注。
- 只有运行真实 DeepSeek/Qwen planner v2 时需要用户配置对应 API key；本轮没有要求或保存任何密钥。
- push、创建 PR、合并或删除 worktree 都需要用户明确选择，本轮不会自动执行。
