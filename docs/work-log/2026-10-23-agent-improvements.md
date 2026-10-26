# 三项 Agent 改进执行记录

## 目标与工作区

执行 2026-10-23 总计划及 A/B/C 分项计划。最终范围仍包括统一网页/CLI/真实数据与 Hybrid Runtime、完整来源归档和回放、冻结数据上的模型收益评测。

- 原目录：`C:\Users\13993\Desktop\大模型学习\Agent`。
- 独立工作区：`C:\Users\13993\Desktop\大模型学习\Agent\.worktrees\agent-improvements`。
- 分支：`codex/agent-improvements`，基于 `c69096a` 并复制当前未提交工作文件。
- 基线清单：聊天工作区 `work/implementation-audit/original-baseline.json`，268 个文件的 SHA-256。原目录未覆盖。
- 使用原项目 `.venv\Scripts\python.exe`，pytest 从独立工作区运行，以该工作区 src 为导入路径。

## A1–A3 当前进度

- 既有 runtime/policy/checkpoint/tools 34 项测试进程退出 0。
- 新增 `test_agent_multitarget.py` 首次为 5 failed/1 passed，复现目标 2/3 只保存 1、部分结果假成功、空搜索反复重试、步骤上限不支持 400。
- 初步修复后，新增 strict 条件、永久移除、清空旧页面、重试上限、最后一步保存的边界测试；首次预期失败后修改代码。
- 最新定向回归涉及 8 个文件，57 项通过，覆盖多岗位、policy、runtime、工具、checkpoint、审批及评测。
- 原有 visual 工具测试默认目标是 10，却仅产生 1 条且断言完成；将该测试显式设为目标 1，保留其所有行为断言。新增独立反例防止目标不足报完成。
- 规格审查进行中；尚未把 A2/A3 全部标记完成。重复岗位、canonical URL 和各种失败状态仍需审查确认。

## 独立任务

- 子代理 Dirac 实施 B1–B3 的独立核心模块与测试，仅写 source_provenance/source_archive/source_replay 三个新模块和对应测试；现有 loader 集成仍由主执行负责。
- 子代理 Faraday 只读审查 A1–A3 规格符合性；之后进行代码质量审查。

## 尚未完成（阶段性记录，后续已关闭大部分）

- A4–A7：共享服务、网页/CLI、报告投影、网页审批及恢复、统一实际验收。
- B：核心实现审查、loader/metadata 接入、实际来源三轮观察及归档回放。
- C1–C7：合法动作空间、按需路由和预算、数据冻结、真实模型公平对照、发布结论。
- 全量测试、覆盖率、release-check、三分钟演示、原目录受控集成。

后续以具体测试与产物更新此记录，不把局部通过当作全计划完成。

## 当前已验证状态（追加记录）

- A1–A3：规格审查及独立质量审查均通过；质量审查者重跑 64 项定向测试通过。多岗位、canonical 身份合并、位置变体、搜索重试、终态与内存仓库并发问题已覆盖。
- A4–A6：共享 start/load/resume 服务及 UI/CLI 接入；网页默认 hybrid+policy。网页会话不存完整结果或 runtime，从持久化记录加载；审批卡片展示编号及批次 hash。真实 provider 即使使用 Demo 数据也占模型会话额度。
- A5：同一 run_id 的报告复用，Hybrid 报告直接写 MarkdownReporter，不调用带业务保存的旧 reporter node。记录逐步预算/usage、打开成功/失败次数、工具耗时、活跃执行耗时和审批等待；未知费用为 null。
- A6：检查点已完成而 context 未完成、批准 gate 后执行前中断、HITL deadline、错误 owner/id/批次和持久 context 配内存仓库等反例已实现。子代理重新验证 runtime/service/UI/HITL/recovery/approval/checkpoint 共 45 项通过。缺少 context 来源引用和旧 CLI checkpoint 升级仍未关闭。
- B：核心归档已加入 OS 锁、序号、精确候选版本及正文上下文绑定、manifest ledger prefix、参数匹配和条件变化门禁。SourceCaptureBrowser 接入 loader 与网页，逐次记录失败及最终成功，保留正文原字节和来源 hint；缓存只保存公开白名单及原观察时间。腾讯、小米、飞书 family 原生 ID/发现请求上下文有专项测试。
- B 集成：共享服务断网回放测试禁止 socket、HTTP 和 planner provider；岗位集合、动作序列与原采集一致。在线/API/缓存/列表证据不混称详情实时成功。剩余项包括旧 reading_replay 格式兼容、来源报告、导出/保留策略、完整在线三轮观察。
- A7：新增 scripts/accept_unified_agent.py 与独立三岗位版本化 fixture；两个子进程验收测试通过，覆盖目标 3、拒绝覆盖旧输出、批准恢复和重复批准。尚需保存正式验收产物及在线结果。
- C：合法动作集、按需路由、请求/Token 预留预算、telemetry、阅读路由和计分核心已加入，独立审查修订持续进行。60 条/30 组数据生产器与配对 runner 由两个写入范围隔离的子代理实施；标签将明确为辅助标注，未获得人类复核前不称正式独立标签。

### 本轮完整回归证据

在独立工作树执行全量 pytest + coverage：**844 passed, 1 skipped in 45.90s，88.52%**，命令退出 0。日志保存于聊天工作区 work/implementation-audit/full-suite-current.log。这一结果只覆盖该次运行时的文件；后续新改动需相应复核，不能用于宣称 A/B/C 全计划完成。

### 后续工作

1. 完成独立审查中剩余合同缺口及来源/CLI 兼容；补齐来源摘要、报告与导出。
2. 保存离线目标 3/不足/拒绝证据、三轮在线来源观察，验证完整断网回放和真实网页运行。
3. 冻结数据、提供可审阅的标签包；完成 reader/planner 分开的 pilot、正式三轮配对及真实任务外部抽查。
4. 更新门禁、默认选型结论、三分钟演示与完成审计；最后按原目录基线 hash 进行受控集成，保留用户原改动。

## 最终收尾记录

- 规格复核发现两处 C5 合同缺口并已关闭：正式 `test` 禁止用 `--exploratory` 绕过人工回执；runner catalog 必须提供完整 `snapshot_by_url`，缺失或候选 URL 覆盖不一致直接拒绝，不再退回排序下标配对。
- v2 数据集为 60 任务 / 30 独立组 / dev 20 / test 40。dev `--validate-only` 通过；test 在缺少 `HUMAN_REVIEW_RECEIPT.json` 时按预期失败，加 `--exploratory` 也在配置校验阶段失败，正式状态保持 `not_run_missing_human_review`。
- DeepSeek 开发 pilot 完成 Planner 与 Reader 三策略对照。`rules` 在 6 条开发任务上保持 6/6 完成、零模型调用；Reader `always` 仅 4/6 且更慢更贵。默认策略继续为 `rules`，结论和边界见 `docs/results/model-value-v2-20261023/DECISION.md`。
- 来源证据已覆盖 Demo 归档验证、content/attempts 断网回放、真实第 1 轮失败复现、正文哈希损坏拒绝，以及三轮真实官方来源观察。三轮均未拿到可交付详情，保留为来源故障证据，不降低过滤口径。
- 离线统一验收已覆盖目标 3、目标不足、HITL 批准/拒绝、重复批准去重和独立进程恢复，均保留版本化产物。
- 最终定向回归：`134 passed`。最终全量回归：`957 passed, 1 skipped`，总覆盖率 `87.75%`，退出码 0。skip 仍是未配置 PostgreSQL 集成环境。
- 全量 Ruff：`src` 与 `tests` 均通过。`web-task-agent --release-check` 的 focused-ruff、pytest-coverage、wheel-build、doctor、strict-hitl 和 git-diff-check 全部通过；`git diff --check` 通过。

### 仍然阻塞的事项

- v2 test 标签仍为 `pending_human_review`，没有真实人工回执，因此不运行正式 40×3×3 对照。
- 10–15 条真实来源任务外部验证尚未完成；三轮各有 7 次成功打开和 6 次失败，最终预算耗尽；先前“全部 removed”的归因已于 2026-10-25 审核纠正，不能代替该验收。
- 当前改动仍未提交；用户未要求 commit、push 或合并。


## 2026-10-25 审核修复收尾

审核发现的运行、来源和评测缺陷已修复。完整发布门禁六项通过，覆盖率 87.99%；日志编码收尾另以 17 项定向测试验证。新 v3 数据冻结并保留旧版本，真实来源本轮仍无交付，不关闭正向在线验收。人工标签回执、正式配对和外部交付核对保持待完成。详见 docs/results/audit-fix-2026-10-25/README.md 和 docs/HUMAN_REVIEW.md。


## 2026-10-25 v4 正式实验完成

用户人工复核及两项口径确认已记录；沿用用户确认的deepseek-chat，响应标识deepseek-flash。Planner和Reader各360单元完整执行，真实API582次、已知Token440231；6个受控空回复不伪记为真实API调用。720个业务SQLite数量复核一致。默认保留rules。10条真实来源任务均无交付，保持正向验收未通过。报告：docs/results/formal-model-value-v4-20261025/DECISION.md。
