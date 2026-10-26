# A：真实来源与 Hybrid Runtime 统一实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 保留现有未提交修改，任务按依赖顺序执行。

**Goal:** 网页、CLI、验收脚本使用同一应用服务和 Hybrid Runtime，正确处理多个岗位、严格筛选、部分完成、审批恢复和报告。

**Architecture:** 先修正 Runtime 的逐候选状态和终态，再将公共组件构建及服务层从 CLI/UI 入口中提取。保留旧 workflow 基线，报告只映射状态、不再次保存岗位；审批使用已有 SQLite checkpoint，重建 runtime 后恢复。

**Tech Stack:** Python、LangGraph、Pydantic、SQLite、Streamlit、现有 RunStore/ArtifactStore。

---

## 文件责任

根目录：`C:\Users\13993\Desktop\大模型学习\Agent`。

| 文件 | 动作与责任 |
|---|---|
| `src/web_task_agent/agent_models.py` | 修改：逐岗位处理状态、结构化候选摘要、运行元数据、步骤上限。 |
| `src/web_task_agent/agent_policy.py` | 修改：多个候选推进、逐 URL 恢复、明确耗尽状态。 |
| `src/web_task_agent/agent_tools.py` | 修改：严格验证、去重、永久错误分类、语义终态。 |
| `src/web_task_agent/agent_runtime.py` | 修改：正确授权和预算，支持服务层驱动现有 start/resume。 |
| `src/web_task_agent/agent_components.py` | 新增：公共 browser/extractor/verifier/matcher/repository/runtime 构建。 |
| `src/web_task_agent/agent_service.py` | 新增：start/resume、结果映射、统一运行调用；不放 Streamlit 渲染代码。 |
| `src/web_task_agent/agent_run_context.py` | 新增：按 owner/run_id 保存恢复所需本地私有上下文。 |
| `src/web_task_agent/agent_cli.py`、`cli.py` | 修改：复用公共构建器和服务，兼容旧 flags。 |
| `src/web_task_agent/streamlit_runner.py`、`streamlit_app.py` | 修改：传入 engine/strategy，展示审批、终态、轨迹。 |
| `src/web_task_agent/run_store.py` | 修改：等待审批和拒绝状态及合法转换。 |
| `src/web_task_agent/reporter.py`、`dashboard.py` | 修改：显示实际完成数、原因和决策轨迹；不调用岗位写入。 |
| `src/web_task_agent/agent_checkpoint.py` | 修改：新增嵌套 Pydantic/Enum 类型时同步 strict msgpack 白名单。 |
| `tests/test_agent_multitarget.py`、`test_agent_search_conditions.py`、`test_agent_service.py`、`test_agent_ui_hitl.py` | 新增：集成与回归测试。 |
| `scripts/accept_unified_agent.py` | 新增：同一服务的离线/真实运行、审批和恢复验收。 |

## A1. 先锁定多岗位失败，建立集成 fixture（约 2h）

**Files:** 新增 `tests/test_agent_multitarget.py`；复用现有 `FakeBrowserClient`、`build_hybrid_runtime`、`WebTaskWorkflow`。

- [x] 写下面的基础 fixture 和测试，先证实失败。此代码完整放进新测试文件，后续测试继续在此文件扩展。

```python
import pytest
from web_task_agent.agent_cli import build_hybrid_runtime
from web_task_agent.browser import FakeBrowserClient
from web_task_agent.extractor import PageExtractor
from web_task_agent.matcher import JobMatcher
from web_task_agent.models import BrowserPage, UserProfile
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow

@pytest.fixture
def multi_workflow(tmp_path):
    pages = [BrowserPage(
        url=f"https://example.com/jobs/{i}",
        title=f"AI Agent Intern {i}",
        content=(f"Title: AI Agent Intern {i}\nCompany: Example AI\n"
                 "Location: Remote\nRequirements: Python LangGraph AI\n"
                 "Responsibilities: Build and test AI agents with Python"),
    ) for i in range(1, 4)]
    repository = JobRepository(tmp_path / "jobs.sqlite")
    repository.initialize()
    return WebTaskWorkflow(
        browser=FakeBrowserClient(pages), extractor=PageExtractor(),
        matcher=JobMatcher(), verifier=JobVerifier(), repository=repository,
        reporter=MarkdownReporter(tmp_path / "reports"),
        enforce_search_conditions=True,
    )

@pytest.mark.asyncio
@pytest.mark.parametrize("target", [1, 2, 3])
async def test_runtime_collects_requested_number(multi_workflow, target):
    urls = [f"https://example.com/jobs/{i}" for i in range(1, 4)]
    state = await multi_workflow.run_with_hybrid_agent(
        UserProfile(keyword="AI intern", location="Remote",
                    target_count=target, seed_urls=urls),
        runtime=build_hybrid_runtime(multi_workflow), max_steps=30,
    )
    assert len(state.verified_jobs) == target
    assert len(state.matches) == target
    assert len(multi_workflow.repository.list_jobs()) == target
    assert state.terminal_status == "completed"
    assert state.terminal_reason == "target_reached"
```

- [x] 执行 `.\.venv\Scripts\python.exe -m pytest tests/test_agent_multitarget.py -v`；预期目标 2/3 首次失败，目标 1 保持通过。
- [x] 添加候选仅 1 个、目标 3 的反例；断言 `partial/candidates_exhausted`，业务库仅保存实际 1 条。
- [x] 添加 0 候选反例；断言零写入、有限步内停止，不调用未配置的公网搜索。
- [x] 保存失败输出，形成仅包含新测试的可审阅补丁；不把当前失败状态当成实现完成。

## A2. 修复候选推进、去重和终态（约 4–6h）

**Files:** 修改 `agent_models.py`、`agent_policy.py`、`agent_tools.py`、`agent_checkpoint.py`；测试 A1 文件和 `test_agent_policy.py`。

- [x] 给状态增加 `processed_urls: set[str]`、`rejected_urls: set[str]`、`current_job_key: str | None`、`page_attempts: dict[str, dict[str, int]]`，均有空默认值。`visited_urls` 仅表示打开成功；打开成功不表示岗位已处理。
- [x] 把 `decide` 的顺序调整为：预算 → 当前失败恢复 → 当前岗位抽取/验证 → 未达目标且有未处理候选则打开下一条 → 候选耗尽或达到目标后匹配 → 保存 → 终止。禁止用全局“已有任一 verified job”跳过新岗位验证。
- [x] 只有 `verified_jobs` 内容变化时使 `matches/saved` 失效；已完成匹配后不要在每次循环无条件清空。拒绝的候选进入 processed 集合，不能反复抽取。
- [x] 去重先用来源岗位 ID（B1 提供），否则用 canonical URL 加规范化公司/标题/工作地；跨城市同一岗位保留一条多工作地记录，不重复计数。多岗位公告未来按 `job_key` 区分，不能按页面 URL 全部覆盖。
- [x] 使用以下终态规则替换“有一条结果就 completed”的逻辑：

```python
def completion_status(*, valid_count: int, target_count: int,
                      saved: bool, stop_reason: str) -> tuple[str, str]:
    if valid_count >= target_count and saved:
        return "completed", "target_reached"
    if valid_count > 0:
        return "partial", stop_reason
    return "failed", stop_reason
```

`stop_reason` 只能来自确定性退出条件：`candidates_exhausted`、`budget_exhausted`、`deadline_exceeded`、`no_candidates`、`storage_error`；拒绝单独为 `rejected/human_denied`。预算耗尽可以交付内存中的部分结果，但不绕过审批自动落库。

- [x] 将 `AgentBudget.max_steps` 上限从 100 调整为 400，并增加 400/401 边界测试。网页自动预算 `min(400, max(12, 6 * target_count + 8))`，目标上限仍为 50；另有 180 秒总时间限制。预算不承诺一定找满。
- [x] 跑 A1、policy、runtime、checkpoint、approval、agent_evaluation 回归；旧 benchmark 因终态定义变更而变化时生成新版本，不覆盖旧报告。

## A3. 统一搜索条件和逐岗位恢复（约 4–6h）

**Files:** 修改 `agent_tools.py::VerifyJobTool/OpenPageTool`、`agent_policy.py::_recover_from_failure/_next_candidate`；新增 `tests/test_agent_search_conditions.py`。

- [x] 增加完整反例矩阵：北京查询遇到上海岗位；AI 公司 HR 岗；地点未知；关键词缺一个；多地岗位；同一岗位重复 URL；第一条岗位视觉恢复后第二条也需视觉恢复；404/410 与 429/超时。
- [x] `VerifyJobTool` 接受 `enforce_search_conditions: bool = False`。公共服务构建时传 True，旧低层调用默认兼容；验证失败原因合并基础 verifier 和已有严格条件函数：

```python
reasons = list(result.reasons)
if self.enforce_search_conditions:
    reasons.extend(job_condition_reasons(
        state.user.keyword, state.user.location, job,
    ))
```

其中 `result = self.verifier.verify(job)`、`job` 为当前待验证岗位；合并后有任一原因即不能进入 verified。测试同时断言写库结果为空，避免只测回复文字。

- [x] `OpenPageTool` 调用已有 `classify_page_exception`；removed 为不可恢复，跳过；timeout/429/5xx 在每 URL 2 次预算内恢复；不支持来源和空正文有独立类别。错误信息保留类型和安全摘要，不记录请求头或响应密钥。
- [x] 文本/视觉尝试以当前 `job_key` 或 URL 分组；单条最多一次 text→visual 恢复，不因历史其他岗位使用过视觉就禁止本岗位恢复。
- [x] `_next_candidate` 删除写死的 `< 2` 兜底，统一使用 `self.max_url_attempts` 与 processed 集合；已验证、已拒绝、已移除候选不可重新排入。
- [x] 跑新反例及已有搜索条件/来源可靠性测试；预期所有入口保持 AND 关键词、OR 城市以及永久移除不降级的语义。

## A4. 提取共享组件和应用服务（约 5–7h）

**Files:** 新增 `agent_components.py`、`agent_service.py`；修改 `agent_cli.py`、`streamlit_runner.py`、`workflow.py`；新增 `tests/test_agent_service.py`。

- [x] 从 `_build_browser` 提取公共 builder，保留 Demo、指定 URL、官方、聚合四种模式，保留公平采样、候选上限、source_diagnostics、resolution_log。UI 的旧函数保留薄委托，避免破坏既有 monkeypatch 测试。
- [x] `build_hybrid_runtime` 的实际实现移到 `agent_components.py`，`agent_cli.py` 重新导出兼容名称，避免 service 依赖 CLI 渲染模块。所有入口传入相同 verifier 配置。
- [x] 在 `agent_service.py` 定义完整配置模型，后续入口只构建此配置：

```python
from typing import Literal
from pydantic import BaseModel, Field, model_validator

class AgentRunOptions(BaseModel):
    engine: Literal["workflow", "hybrid"] = "workflow"
    planner_strategy: Literal["policy", "always", "adaptive"] = "policy"
    planner_provider: Literal["deepseek", "qwen"] | None = None
    planner_model: str | None = None
    hitl: bool = False
    max_steps: int = Field(default=30, ge=1, le=400)
    deadline_seconds: float = Field(default=180, gt=0, le=600)

    @model_validator(mode="after")
    def validate_options(self):
        if self.hitl and self.engine != "hybrid":
            raise ValueError("HITL requires hybrid engine")
        if self.planner_strategy != "policy" and not self.planner_provider:
            raise ValueError("LLM strategy requires a provider")
        return self
```

`adaptive` 在 C1/C2 完成之前不可作为可选生产入口；服务在此阶段收到它应明确返回不支持配置，而不是静默改成 always。

- [x] 服务提供三个明确操作：`start_agent_run(request, *, run_id, owner_id, services, options)`、`resume_agent_run(*, run_id, owner_id, approval_decision, services)`、`load_agent_run(*, run_id, owner_id, services)`。`request` 复用 `UiRunRequest` 的业务字段但将共享请求模型移动到 service 避免循环 import；UI 重新导出原名。`services` 是公共 builder 提供的 repository/browser/store 依赖集合。
- [x] 测试用两个入口调用同一注入服务，比较 run_id、engine、终态、规范化岗位集合和轨迹；不比较真实时间或随机 UUID。用 spy 确认 Hybrid 分支实际调用 `HybridAgentRuntime`，不能仅验证 JSON 字段写了 hybrid。
- [x] 旧 `run_with_langgraph` 保留为 engine=workflow 基线。离线全回归通过之后才能切网页默认 engine；CLI 历史命令继续保持原默认。

## A5. 结果映射和报告只写一次（约 3–4h）

**Files:** `agent_service.py`、`streamlit_runner.py`、`reporter.py`、`dashboard.py`、`agent_cli.py`。

- [x] 将 Hybrid 状态映射到展示结果：`jobs=verified_jobs`、`matches=matches`、实际 open 成功/失败次数、唯一候选数、终态和退出原因。`tool_latency` 与端到端耗时分别记录，Token 未知为 null，不填“0 元”。
- [x] 使用现有 `MarkdownReporter.write_report`，显式传入 user、jobs、matches、metrics、execution_trace，设置 `orchestration_mode="hybrid"`。禁止调用 `workflow._reporter_node`：该节点有业务写库行为，会绕过统一保存/审批。
- [x] 将原 `execution_trace: list[dict[str,str]]` 兼容字段保留给旧 UI；新增结构化 `agent_trace` 字段保存 action、source、target、observation、remaining_budget、planner_usage。旧记录缺失时返回空列表。
- [x] 测试报告、Dashboard、action plan、result.json 的 run_id 一致；调用 spy 断言每个逻辑保存只有一个 receipt。保存成功、拒绝和部分结果分别写快照测试，不以 HTML 整页逐字符匹配。
- [x] UI 使用实际终态判定完整/部分结果；“没有报错”不能自动等于已满足目标。下载报告同时显示来源时间和模式，B2 接入后写 archive manifest 引用。

## A6. 网页审批与跨进程恢复（约 6–8h）

**Files:** 新增 `agent_run_context.py`、`tests/test_agent_ui_hitl.py`；修改 `agent_service.py`、`agent_checkpoint.py`、`run_store.py`、`streamlit_app.py`。

- [x] `RunStatus` 新增 `awaiting_approval/rejected`，合法转换：running→awaiting_approval；awaiting_approval→running/rejected/failed；拒绝为终态。File/PostgreSQL adapter 共用转换规则，老记录仍可读取。检查部署 SQL 是否有 status CHECK；若有，增加显式向前迁移脚本，不假定仅改 Python 就完成。
- [x] 私有 `run-context.json` 保存 run_id、owner_id、thread_id、原业务条件、固定候选、来源 manifest、options、checkpoint 路径和状态 schema 版本；API Key 从环境重新取。私有文件不进入下载白名单，不放进公开报告。
- [x] start 在 `open_sqlite_checkpointer` 上下文内调用现有 `start_hitl`；到 interrupt 后序列化摘要并关闭连接。Streamlit session_state 只保留 run_id、owner_id，不持有 runtime、事件循环或数据库连接。
- [x] resume 先按 owner/run_id 读 context，再按原配置重建组件，调用已有 `resume_hitl`；不得重新搜索或重新生成待审批岗位。审批卡片显示岗位摘要、数量、内容摘要 hash 和 approval_id，批准必须对应同一不可变结果批次。
- [x] 重复批准返回“已处理”状态并加载已存结果，不再执行写操作；错误 owner、approval_id 不匹配、过期 context 均拒绝恢复。并发 resume 使用本地 run 锁和业务 receipt；不宣称跨实例分布式恰好一次。
- [x] 用两个独立 Python 进程验证 start/resume；另测 reject、重复 resume、owner 不匹配、页面 rerun 和改条件后批准旧卡片。拒绝时允许保存审计元数据，业务 jobs 表必须为零。
- [x] 页面新增批准/拒绝按钮和待审批历史行；旧无 HITL 默认流程仍可用。若目标部署为多实例，需单独解决共享 checkpoint；本轮只验收本地/单实例。

## A7. 一条可审计的真实运行与发布切换（约 3–5h）

**Files:** 新增 `scripts/accept_unified_agent.py`、`evaluations/unified-agent-v1/pages.json`；修改 README、`docs/interview-benchmark-story.md`、`scripts/showcase_run.py`。

- [x] 脚本使用 service 而非复制主链路，定义 flags：`--mode demo|official|aggregator|seed_urls`、`--keyword`、`--location`、`--target-count`、`--planner-strategy policy|always|adaptive`、`--provider`、`--hitl`、`--run-id`、`--resume approve|reject`、`--approval-id`、`--output-dir`、`--deadline-seconds`。start 与 resume 参数互斥检查；输出目录存在时不覆盖。
- [x] `mode=demo` 的验收脚本加载 `evaluations/unified-agent-v1/pages.json`，内容为 A1 三条完整 Remote 岗位的版本化 JSON 数组，不直接复用生产 DEMO_JOB_PAGES：现有 Demo 包含列表页和弱结构页面，不能保证同一工作地有 3 条合格岗位。网页原 Demo 保留并如实显示不足。
- [x] 先跑离线目标 3，验证完整链路、部分结果和审批反例；再用同一服务跑真实官方来源，目标 3，真实返回不足也保留，不降低过滤口径。
- [x] A/B 交接后保存归档 manifest。真实模型策略选择使用 C 的预算模块；C 未结束前可运行有边界的 always 实验，但生产默认 policy。
- [x] 网页默认切为 hybrid+policy，保留高级设置中的 workflow 回归入口；真实模型启用由用户明确选择或 C 的默认选型决定。不得把 provider 缺失静默变为成功的 mock 运行。
- [x] 更新文档删除“网页尚未接 Hybrid”的旧描述时，必须附真实 run_id 与验收文件；演示脚本展示 1 次正常、1 次失败恢复、1 次拒绝。

## 执行命令与验收

每个任务按“写反例 → 首次失败 → 最小修改 → 定向通过 → 审阅补丁”执行。新接口测试放在对应任务文件，不依赖公网。

```powershell
Set-Location -LiteralPath 'C:\Users\13993\Desktop\大模型学习\Agent'
.\.venv\Scripts\python.exe -m pytest tests/test_agent_multitarget.py tests/test_agent_search_conditions.py tests/test_agent_service.py tests/test_agent_ui_hitl.py tests/test_agent_runtime.py tests/test_agent_checkpoint.py tests/test_search_conditions_runner.py tests/test_source_reliability_runner.py -v
.\.venv\Scripts\python.exe scripts/accept_unified_agent.py --mode demo --keyword 'AI intern' --location Remote --target-count 3 --planner-strategy policy --output-dir portfolio-artifacts/unified-agent-v1-offline
.\.venv\Scripts\python.exe scripts/accept_unified_agent.py --mode official --keyword 'AI Agent 实习' --location '北京、上海' --target-count 3 --planner-strategy policy --deadline-seconds 180 --output-dir portfolio-artifacts/unified-agent-v1-live
```

上面新增测试/脚本命令是实施后的验收接口，当前尚不存在，不在本次规划阶段声称通过。期望离线目标 3 满足并且只有一次保存；在线按事实记录结果数，不预设一定满额。A 关闭条件为：多岗位/条件/终态/恢复/审批全部覆盖，网页和真实脚本实际走同一 runtime，B 来源引用进入产物。
