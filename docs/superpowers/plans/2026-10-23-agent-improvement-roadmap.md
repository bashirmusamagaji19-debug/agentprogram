# Web Agent 三项改进总计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本项目当前含大量未提交改动，实施时优先在当前上下文内顺序执行；任何并行任务必须先确认写入范围不重叠。

**Goal:** 贯通真实岗位来源与 Hybrid Agent，建立完整来源证据和离线回放，并用公平对照确定模型介入的实际收益。

**Architecture:** 复用现有 Browser、Extractor、Verifier、Matcher、Hybrid Runtime、HITL 和持久化适配器，新增一个共享应用服务，供网页、CLI 和验收脚本调用。来源证据作为独立版本化数据贯穿发现、取正文、抽取和报告；评测分离来源、阅读、规划与推荐，避免混合归因。

**Tech Stack:** 当前项目 Python 3.12.7 本地环境、LangGraph、Pydantic、SQLite、Streamlit、pytest；CI 继续验证 Python 3.11。真实模型使用已有 provider 适配器，不增加框架。

---

## 1. 本次交付和阅读顺序

本计划的代码、离线验收和开发 pilot 已有实现，2026-10-25 审核发现的问题正在修复，当前不能按全部完成验收。阅读顺序：本总计划 → A → B → C。人工标签复核和真实交付核对见 `docs/HUMAN_REVIEW.md`；正式 test 是依赖人工回执的机器任务，不用开发 pilot 冒充正式结论。

| 文件 | 解决的问题 | 核心交付 |
|---|---|---|
| [A：统一运行链路](2026-10-23-agent-runtime-integration.md) | 网页真实数据与 Hybrid 核心分离 | 多岗位正确执行、共享应用服务、网页审批、同一条真实运行轨迹 |
| [B：来源证据与回放](2026-10-23-source-provenance-replay.md) | 只有 URL 无法复现采集 | 发现上下文、版本化正文、逐次解析轨迹、断网回放、来源分层报告 |
| [C：模型收益评测](2026-10-23-model-value-evaluation.md) | 缺少模型收益的公平证据 | 分组冻结数据、规则/全量/按需对照、调用预算、逐题指标、选型结论 |

项目根目录：`C:\Users\13993\Desktop\大模型学习\Agent`。同级 `visual-web-agent` 是既有视觉适配依赖，本轮不重构。文中源文件路径均相对此项目根目录，函数名用于定位，行号会随现有未提交改动变化。

## 2. 2026-10-23 本地核查结论

基线分支为 `fix/source-reliability-finish`，HEAD 为 `c69096a5826ac6aa3829086ff504fba57c30b04d`。该 HEAD 不包含当前全部代码；必须同时保留 dirty 状态和工作文件哈希。

| 证据 | 现状与计划影响 |
|---|---|
| `streamlit_runner.py::run_ui_request` | 仍调用 `workflow.run_with_langgraph`，网页未统一到 Hybrid Runtime。 |
| `workflow.py::run_with_hybrid_agent`、`agent_cli.py::build_hybrid_runtime` | 已有可复用入口；不重新实现 Agent 图。 |
| `agent_tools.py::VerifyJobTool` | 只调基础 verifier，未接网页使用的 `job_condition_reasons`；迁移会有关键词/地点口径退化风险。 |
| `agent_policy.py::decide` | 当目标为 2、已有 1 条验证结果且另有候选时，下一步是 `score_match`，而非继续处理候选。 |
| 实际离线 Runtime 复现 | 两个有效 fixture、目标 2、预算 30，最终仅验证/保存 1 条，状态却为 `completed/no_action_available`。动作链为 `open_page → extract_text → verify_job → score_match → save_results → finish`。 |
| `agent_policy.py::_recover_from_failure` | visual 是否使用过按全局 observation 判断；应改为逐岗位/逐页面判断，避免一个岗位的视觉恢复影响另一个岗位。 |
| `job_sources.py::AggregatorPageLoader` | 已有缓存、官方 API、HTTP、列表正文降级及 resolution log；B 是扩充证据契约，不重写 loader。 |
| `page_reliability.py`、`search_conditions.py` | 已有移除信号、条件过滤和来源诊断，A/B 必须保留。 |
| `evidence_reader.py`、`reading_replay.py` | 10-17 已完成带引文阅读与正文回放实验，应复用并扩展输入格式。当前生产默认未接入。 |
| 10-17 阅读实验日志 | 34 条页面记录、29 个唯一输入；有标签纠正和大量待复核，不能作为已验证整体准确率的依据。 |
| `run_store.py`、`artifact_store.py` | File/PostgreSQL/S3 边界已存在；本轮需要扩展等待审批状态，不以重新部署云端为前置条件。 |

本次运行了 9 个相关测试文件的定向离线回归：输出达到 100%，进程退出码 0。未运行全量覆盖率，不把 README 的历史 `632 passed/87.35%` 当成本次复核结果。上述多岗位问题由额外实际运行发现，说明既有测试通过并不覆盖新目标。

基线命令（已执行）：

```powershell
Set-Location -LiteralPath 'C:\Users\13993\Desktop\大模型学习\Agent'
.\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime.py tests/test_agent_policy.py tests/test_agent_checkpoint.py tests/test_agent_approval.py tests/test_streamlit_runner.py tests/test_search_conditions_runner.py tests/test_source_reliability_runner.py tests/test_evidence_reader.py tests/test_reading_replay.py -q -p no:cacheprovider
```

## 3. 最终产品验收场景

固定用户任务：搜索“AI Agent 实习”，工作地“北京、上海”，目标 3，使用合成候选人资料。正式在线演示用实际发现结果，不以 fixture 代替；离线验收使用冻结内容，明确标注回放模式。

1. 网页、CLI、验收脚本调用同一个应用服务；结果标明 engine、planner strategy 和 reader strategy。
2. Agent 根据当前候选、失败和预算执行；候选足够时返回 3 条，候选不足时返回实际数量并标明原因。
3. 每条结果满足关键词 AND、城市 OR 条件；未知工作地不得混入限定城市结果。
4. 每条结果有可追溯的发现记录、正文版本、内容来源和验证结果；列表来源证明不等于实时详情可访问。
5. 打开失败、移除、抽取失败、地点冲突分别进入可解释分支；404/410 不被旧列表正文“恢复”为在招。
6. 开启审批时，网页暂停并展示待保存结果摘要；刷新或重启后可恢复；拒绝不写业务岗位表，重放不重复保存。
7. 同一来源归档可完全断网回放；回放不访问搜索、原站或模型，结果携带原采集时间。
8. 规则、全量模型、按需模型在相同候选和预算下比较；报告允许结论为“模型无收益，保留规则默认”。

## 4. 不变量

- 默认关键词与工作地规则不放宽；历史 workflow 作为回归和 A/B 基线保留。
- `visited`、`processed`、`failed`、`verified` 不混为一个集合；达到目标后才提前收敛，未达到目标必须有明确耗尽原因。
- `completed` 必须满足目标及持久化语义；部分结果不能因“循环结束”而报完整成功。
- 候选最多 100、目标最多 50、每 URL 打开最多 2 次；步骤、模型请求数、Token 预留、总时间都有限额。
- 同一内容可以复用模型读取，但不同城市/关键词的判定必须分别执行；多个岗位不能按页面 URL 互相覆盖。
- 模型只能建议授权动作，不能变更筛选规则、审批状态、来源真实性或预算。
- 等待审批期间不保持 SQLite 连接和 Python runtime 对象存活；恢复时重建，依据 checkpoint 和 run context。
- 历史数据不覆盖；评测输入、标签修订、模型输出和失败均可追溯；没有 usage 不记为零成本。

## 5. 实施顺序与工作量

以下是集中开发的工时估计，包含编码、定向测试和报告，不包含等待人工标注、外部 API 恢复或用户学习时间；总计约 70–100 小时，建议 3–4 周完成。

| 阶段 | 任务 | 工时 | 交付门槛 |
|---|---|---:|---|
| P0 | 保存基线；A1–A3：多岗位、终态、条件与恢复 | 10–14h | 目标 1/2/3、部分结果、永久失败测试通过 |
| P1 | B1–B3：来源契约、归档、断网回放 | 12–16h | 离线 fixture 全部可追溯，破损哈希被拒绝 |
| P2 | A4–A7：共享服务、网页/CLI、审批和报告 | 18–24h | 同一 runtime 可在网页 start/resume/reject |
| P3 | B4–B5：实际来源留档与三轮观察 | 6–10h | 取正文模式和详情可达性分别报告 |
| P4 | C1–C4：合法动作集、按需路由、遥测、数据集 | 14–20h | 路由决策可解释、测试集冻结、调用预算有效 |
| P5 | C5–C7：公平对照、真实抽查、选型和演示 | 10–16h | 得到可复现结果和允许负结果的决策报告 |

依赖链：A1 → A2 → A3 → A4；B1 → B2 → B3；A4 与 B3 交接后做 A5/A6/A7；B4/B5 和 C1/C2 可穿插；C3 数据冻结后再 C5 正式评测。不得边看测试集结果边修改门禁，然后把同一集合当未见测试集。

## 6. 范围控制和迁移

本轮集中解决三个问题，不以增加 Multi-Agent、RAG、Skills、自动投递或重做前端为完成条件。已存在的云存储 adapter 和视觉 provider 保留；视觉实验需单独标注真实 provider 或确定性 adapter。

当前有大量已有未提交改动，实施前保存 `git status --porcelain=v1`、`git diff --stat`、源码和测试哈希；只对当次任务文件做显式暂存，避免 `git add .`。不要 hard reset、clean 或覆盖现有未跟踪实验文件。每个任务形成可独立审阅的补丁；只有确认原有改动归属后才提交，提交频率不替代变更归属审查。

迁移顺序：共享服务先保持 `workflow` 兼容；增加 `hybrid` 可选；满足 A 的离线和 B 的来源门槛后，网页默认切 `hybrid + policy`。真实模型的默认启用由 C 的结论决定，不提前切成全量 LLM。历史 CLI flags 与输出字段兼容，新增字段均有默认值。

## 7. 发布门禁与失败处理

最终执行：

```powershell
Set-Location -LiteralPath 'C:\Users\13993\Desktop\大模型学习\Agent'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --cov=web_task_agent --cov-report=term-missing --cov-fail-under=70
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\web-task-agent.exe --release-check
git diff --check
```

预期：离线测试无非预期失败；覆盖率达到现有 70% 门槛且不为过门槛删除分支；lint/whitespace 通过；release-check 的命令范围同步新增模块。PostgreSQL 条件测试可以因无测试实例跳过，但报告列出原因，不称云端验收完成。若 release-check 因原有混合改动的 Git 状态策略失败，单独记录，不能通过丢弃原有改动解决。

在线运行的失败也属于证据。若没有模型 Key，完成所有离线测试及预算/指标验证，真实模型结果标记 `not_run_missing_credentials`；不得用 mock 结果补齐。来源故障不阻塞冻结回放和纯评测代码，但最终在线验收项保持未完成。

## 8. 最终证据清单

- [x] 基线清单：HEAD、dirty manifest、环境版本、数据与代码哈希。
- [ ] 一次网页 Hybrid 真实运行：候选 → 决策 → 验证 → 匹配 → 保存，统一 run_id。当前真实来源运行未交付，不能以失败/空结果关闭正向验收。
- [x] 目标 3 的离线端到端结果，以及目标不足的 `partial` 结果。
- [x] 网页批准/拒绝、不同进程恢复、replay 去重、owner 归属四类证据。
- [x] 来源归档与哈希；三轮在线观察；断网回放；损坏/过期/移除反例。
- [x] 冻结的数据集、标注规范、开发/测试分组和修订历史。
- [x] 正式 Planner、Reader 分开的配对对照，含模型调用、失败、Token 与耗时；Matcher 维持既有规则基线。开发 pilot 与 v4 正式实验均已保存；14条人工回执通过。真实在线正向验收仍未通过。
- [x] 全量回归、覆盖率、lint、release-check 与三分钟演示脚本。
- [x] 结论仅覆盖实测环境；若模型无收益，明确保留规则默认并说明何时调用模型。

**计划完成不等于功能完成。** 复选框逐项以实际代码、命令输出和运行产物关闭；人工门禁和在线正向结果未满足的项保持未勾选。
