# 开放互联网岗位搜索 Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 Hybrid Web Task Agent 上增加开放互联网岗位搜索、搜索结果来源验证、浏览器详情证据、Web Agent 运行台和可复现评测，使用户可以输入自然语言岗位需求并得到真实、可审计的岗位结果或可信零结果。

**Architecture:** 保留现有 `WebTaskWorkflow` 和 `HybridAgentRuntime`，新增 `SearchIntent`、搜索 provider、候选页面验证、证据绑定和开放搜索编排层。在线模式使用 Tavily 发现 URL，浏览器负责详情验证；离线模式使用 fixture provider 和本地页面。前端通过一个轻量 HTTP API 读取运行状态和审计 artifact，不把业务判断放到浏览器端。

**Tech Stack:** Python 3.11, Pydantic 2, existing LangGraph runtime, browser-use/Playwright adapter, SQLite, FastAPI + Uvicorn, vanilla HTML/CSS/JavaScript, pytest/pytest-asyncio/ruff.

---

## 文件结构与责任边界

### 新增

- `src/web_task_agent/open_search/models.py`: `SearchIntent`、候选 URL、页面证据、失败和运行结果模型。
- `src/web_task_agent/open_search/query_parser.py`: 自然语言到 `SearchIntent` 的 deterministic/demo/provider 边界。
- `src/web_task_agent/open_search/search_provider.py`: `SearchProvider` 协议、Tavily provider、fixture provider。
- `src/web_task_agent/open_search/source_verifier.py`: 官方域名/公开 ATS 归属与页面类型判断。
- `src/web_task_agent/open_search/evidence.py`: 页面字段证据和内容哈希。
- `src/web_task_agent/open_search/pipeline.py`: 搜索、验证、抽取、过滤、匹配、去重和 artifact 编排。
- `src/web_task_agent/open_search/artifacts.py`: JSON/JSONL/Markdown 运行 artifact 写入与读取。
- `src/web_task_agent/open_search/api.py`: FastAPI 应用、任务创建、状态、结果和轨迹接口。
- `src/web_task_agent/open_search/web/index.html`: Agent 运行台四视图。
- `tests/open_search/test_models.py`: 新领域模型测试。
- `tests/open_search/test_query_parser.py`: 需求解析测试。
- `tests/open_search/test_search_provider.py`: provider、key 和预算测试。
- `tests/open_search/test_source_verifier.py`: 来源与页面类型测试。
- `tests/open_search/test_evidence.py`: 证据、哈希和字段绑定测试。
- `tests/open_search/test_pipeline.py`: 离线端到端和失败终态测试。
- `tests/open_search/test_api.py`: API 输入、状态、错误和轨迹测试。
- `data/open-search/fixtures/`: 4 个来源的候选结果、本地岗位页面和 40 个冻结样本清单。
- `data/open-search/evaluation/queries.jsonl`: 20 条人工标注自然语言需求。
- `docs/results/open-search/`: 评测结果、在线运行审计和指标口径。

### 修改

- `pyproject.toml`: 增加 `fastapi`、`uvicorn` 和搜索 provider 所需的最小依赖，加入 `open-search` CLI entrypoint。
- `src/web_task_agent/models.py`: 只在确有消费者需要时增加证据引用字段，保持旧 workflow 模型兼容。
- `src/web_task_agent/cli.py`: 增加 `--open-search`、`--open-search-demo`、`--open-search-api` 和 artifact 输出参数。
- `README.md`: 将开放搜索 Agent 作为主项目故事，保留 fixture/真实指标边界。
- `docs/interview-benchmark-story.md`: 增加开放搜索、来源验证和真实故障的讲述。
- `.env.example`: 增加 `TAVILY_API_KEY`，只写变量名和说明。

## 实施顺序

### Task 1: 建立开放搜索领域模型

**Files:**
- Create: `src/web_task_agent/open_search/__init__.py`
- Create: `src/web_task_agent/open_search/models.py`
- Test: `tests/open_search/test_models.py`

- [ ] **Step 1: 写失败测试，固定模型契约**

```python
def test_search_intent_normalizes_unique_constraints():
    intent = SearchIntent(
        raw_text="找北京 Agent 实习",
        role_keywords=["Agent", "agent"],
        locations=["北京", "北京"],
        required_skills=["Python"],
        preferred_skills=["LangGraph"],
        excluded_roles=["产品经理"],
        target_count=5,
    )
    assert intent.role_keywords == ["Agent"]
    assert intent.locations == ["北京"]

def test_failure_record_requires_code_and_url():
    with pytest.raises(ValidationError):
        FailureRecord(code="page_unavailable", url="", message="timeout")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\pytest.exe tests\open_search\test_models.py -q`

Expected: FAIL because `SearchIntent` and `FailureRecord` do not exist.

- [ ] **Step 3: 实现最小模型**

在 `models.py` 定义并导出：`SearchIntent`、`SearchCandidate`、`FieldEvidence`、`VerifiedJob`、`FailureRecord`、`SearchRunSummary`。所有字符串字段 trim，列表去重，`target_count` 范围为 1–20，`content_hash` 必须是非空十六进制字符串。

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\pytest.exe tests\open_search\test_models.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add src/web_task_agent/open_search tests/open_search/test_models.py
git commit -m "feat: add open search domain models"
```

### Task 2: 实现需求解析与搜索 provider

**Files:**
- Create: `src/web_task_agent/open_search/query_parser.py`
- Create: `src/web_task_agent/open_search/search_provider.py`
- Modify: `pyproject.toml`, `.env.example`
- Test: `tests/open_search/test_query_parser.py`, `tests/open_search/test_search_provider.py`

- [ ] **Step 1: 先写解析和 provider 失败测试**

```python
def test_demo_parser_extracts_location_skills_and_exclusions():
    intent = DemoQueryParser().parse(
        "找北京或远程 Agent 实习，要求 Python、LangGraph，排除产品经理"
    )
    assert intent.locations == ["北京", "远程"]
    assert "Python" in intent.required_skills
    assert "产品经理" in intent.excluded_roles

@pytest.mark.asyncio
async def test_tavily_provider_requires_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(SearchProviderConfigurationError):
        TavilySearchProvider.from_environment()

@pytest.mark.asyncio
async def test_fixture_provider_returns_bounded_candidates():
    result = await FixtureSearchProvider(fixtures=[candidate]).search("Agent 北京")
    assert len(result) == 1
    assert result[0].url.startswith("https://")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\pytest.exe tests\open_search\test_query_parser.py tests\open_search\test_search_provider.py -q`

Expected: FAIL because parser/provider modules do not exist.

- [ ] **Step 3: 实现 deterministic parser 和 provider 协议**

`QueryParser.parse(text)` 返回 `SearchIntent`；先实现中文关键词、地点、技能和排除词规则，保留 `LlmQueryParser` 注入边界但不要求在线 LLM。`SearchProvider.search(query, limit)` 为异步协议。`TavilySearchProvider` 使用 `httpx.AsyncClient` 调 Tavily endpoint，脱敏记录 query、结果数、延迟和错误码；不得记录 API key。

- [ ] **Step 4: 增加依赖和环境变量说明**

在 `pyproject.toml` 增加 `httpx>=0.27`、`fastapi>=0.115`、`uvicorn>=0.30`；`.env.example` 只增加 `TAVILY_API_KEY=` 和“在线搜索可选”的注释。

- [ ] **Step 5: 运行测试和静态检查**

Run: `.\.venv\Scripts\pytest.exe tests\open_search\test_query_parser.py tests\open_search\test_search_provider.py -q`

Expected: PASS。再运行 `.\.venv\Scripts\ruff.exe check src/web_task_agent/open_search`，Expected: no errors。

- [ ] **Step 6: 提交**

```powershell
git add pyproject.toml .env.example src/web_task_agent/open_search tests/open_search/test_query_parser.py tests/open_search/test_search_provider.py
git commit -m "feat: add query parser and search providers"
```

### Task 3: 实现来源验证、浏览器详情和字段证据

**Files:**
- Create: `src/web_task_agent/open_search/source_verifier.py`
- Create: `src/web_task_agent/open_search/evidence.py`
- Modify: `src/web_task_agent/browser.py`, `src/web_task_agent/extractor.py` only when existing abstractions cannot expose final URL/title/content metadata.
- Test: `tests/open_search/test_source_verifier.py`, `tests/open_search/test_evidence.py`

- [ ] **Step 1: 写失败测试覆盖可信来源和证据规则**

```python
def test_official_greenhouse_url_is_trusted():
    verdict = SourceVerifier().verify_url(
        "https://job-boards.greenhouse.io/example/jobs/123"
    )
    assert verdict.trusted is True
    assert verdict.source_type == "public_ats"

def test_search_result_page_is_rejected():
    verdict = SourceVerifier().verify_url("https://www.google.com/search?q=agent+intern")
    assert verdict.trusted is False
    assert verdict.failure_code == "source_untrusted"

def test_field_evidence_hash_is_stable():
    evidence = build_field_evidence("title", "Agent Intern", source_text="...")
    assert evidence.content_hash == build_content_hash("...")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\pytest.exe tests\open_search\test_source_verifier.py tests\open_search\test_evidence.py -q`

Expected: FAIL because verifier/evidence functions do not exist.

- [ ] **Step 3: 实现来源验证**

维护可解释的官方 ATS 规则：公司官网招聘域名、Greenhouse、Workday、Lever 和已知品牌 ATS；对搜索引擎、聚合招聘站、新闻和培训页返回 `source_untrusted`。验证结果必须包含 `normalized_url`、`source_type`、`reason` 和 `failure_code`。

- [ ] **Step 4: 实现证据绑定和内容哈希**

`build_content_hash(text)` 使用 SHA-256；`FieldEvidence` 保存字段名、规范化值、证据片段、页面 URL 和哈希。抽取字段缺失时返回空字段和 `extraction_incomplete`，不得让模型补写。

- [ ] **Step 5: 运行测试确认通过并回归旧测试**

Run: `.\.venv\Scripts\pytest.exe tests\open_search\test_source_verifier.py tests\open_search\test_evidence.py tests\test_browser.py tests\test_extractor.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add src/web_task_agent/open_search src/web_task_agent/browser.py src/web_task_agent/extractor.py tests/open_search tests/test_browser.py tests/test_extractor.py
git commit -m "feat: verify job sources and bind page evidence"
```

### Task 4: 实现开放搜索端到端 pipeline

**Files:**
- Create: `src/web_task_agent/open_search/pipeline.py`
- Create: `src/web_task_agent/open_search/artifacts.py`
- Create: `tests/open_search/test_pipeline.py`
- Create: `data/open-search/fixtures/` fixture manifest and four source samples

- [ ] **Step 1: 写离线成功、失败和预算终态测试**

```python
@pytest.mark.asyncio
async def test_pipeline_returns_verified_jobs_and_trace(tmp_path):
    result = await OpenSearchPipeline(...).run(SearchIntent(...), output_dir=tmp_path)
    assert result.summary.terminal_reason == "target_reached"
    assert result.jobs[0].evidence
    assert (tmp_path / "execution-trace.jsonl").exists()

@pytest.mark.asyncio
async def test_pipeline_separates_no_match_from_search_failure(tmp_path):
    result = await OpenSearchPipeline(provider=FailingSearchProvider()).run(...)
    assert result.summary.terminal_reason == "search_api_error"
    assert result.failures[0].code == "search_api_error"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\pytest.exe tests\open_search\test_pipeline.py -q`

Expected: FAIL because pipeline and artifact writer do not exist.

- [ ] **Step 3: 实现 pipeline 的有限状态流**

按设计顺序调用 parser 输出、provider、`SourceVerifier`、现有 `BrowserClient`、`PageExtractor`、`JobVerifier`、`JobMatcher` 和去重逻辑。每个候选 URL 写一条 trace；失败继续处理其他候选，预算耗尽后进入 `budget_exhausted`。只有有完整证据且满足硬约束的岗位才能进入 `jobs`。

- [ ] **Step 4: 实现 artifact writer**

以临时目录写入后原子替换 `run-summary.json`、`search-queries.jsonl`、`candidate-pages.jsonl`、`jobs.jsonl`、`execution-trace.jsonl`、`failures.jsonl` 和 `evaluation-summary.md`。JSONL 每行必须是对应 Pydantic 模型的 JSON。

- [ ] **Step 5: 写四个来源 fixture 和异常样本**

至少包含百度内嵌数据、NVIDIA Workday、联想官方详情和一个动态页面 fixture；同时包含非岗位页、404、缺失字段、地点不匹配和重复 URL。fixture 只用于离线测试，不计入真实在线指标。

- [ ] **Step 6: 运行 pipeline 测试和旧测试**

Run: `.\.venv\Scripts\pytest.exe tests\open_search\test_pipeline.py tests\test_agent_runtime.py tests\test_agent_tools.py tests\test_workflow.py -q`

Expected: PASS。

- [ ] **Step 7: 提交**

```powershell
git add src/web_task_agent/open_search data/open-search/fixtures tests/open_search/test_pipeline.py
git commit -m "feat: add auditable open search pipeline"
```

### Task 5: 接入 Hybrid Agent 工具和 CLI

**Files:**
- Modify: `src/web_task_agent/agent_models.py`, `src/web_task_agent/agent_tools.py`, `src/web_task_agent/agent_policy.py`, `src/web_task_agent/agent_cli.py`, `src/web_task_agent/cli.py`
- Test: `tests/test_agent_models.py`, `tests/test_agent_tools.py`, `tests/test_agent_policy.py`, `tests/test_agent_cli.py`, `tests/open_search/test_pipeline.py`

- [ ] **Step 1: 写失败测试固定开放搜索工具授权**

```python
def test_policy_limits_search_queries_and_candidates():
    state = build_open_search_state(max_queries=4, max_candidates=20)
    decision = DeterministicAgentPolicy(...).decide(state)
    assert decision.action is AgentAction.SEARCH_WEB
    assert decision.arguments["query_budget"] == 4

def test_invalid_planner_search_target_falls_back_to_policy():
    result = run_hybrid_fixture_with_invalid_search_decision()
    assert result.metrics.fallback_decisions == 1
    assert result.terminal_reason in {"target_reached", "no_trusted_match", "budget_exhausted"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\pytest.exe tests\test_agent_models.py tests\test_agent_tools.py tests\test_agent_policy.py tests\test_agent_cli.py -q`

Expected: FAIL because open-search actions and arguments are not registered.

- [ ] **Step 3: 增加类型化工具和 policy 边界**

将 `search_web`、`verify_source`、`verify_evidence` 映射到现有 registry；policy 强制查询上限、候选上限、URL 重试和 `finish` 终态。`save_result` 继续使用现有 `approval_id` receipt 机制。

- [ ] **Step 4: 增加 CLI 入口和运行模式**

增加：

```powershell
.\.venv\Scripts\web-task-agent.exe --open-search-demo --query "找北京 Agent 实习"
.\.venv\Scripts\web-task-agent.exe --open-search-api --query "找北京 Agent 实习" --output-dir outputs\open-search
```

没有 `TAVILY_API_KEY` 时在线模式退出码为 2 并打印配置错误；离线模式必须无需 key 完成并写 artifact。

- [ ] **Step 5: 回归测试并提交**

Run: `.\.venv\Scripts\pytest.exe tests\test_agent_models.py tests\test_agent_tools.py tests\test_agent_policy.py tests\test_agent_cli.py tests\open_search -q`。

Expected: existing suite and open-search suite PASS。

```powershell
git add src/web_task_agent/agent_models.py src/web_task_agent/agent_tools.py src/web_task_agent/agent_policy.py src/web_task_agent/agent_cli.py src/web_task_agent/cli.py tests
git commit -m "feat: expose open search through hybrid agent and cli"
```

### Task 6: 构建 Web Agent 运行台

**Files:**
- Create: `src/web_task_agent/open_search/api.py`
- Create: `src/web_task_agent/open_search/web/index.html`
- Create: `tests/open_search/test_api.py`
- Modify: `pyproject.toml` script entrypoint if required.

- [ ] **Step 1: 写 API 契约测试**

```python
def test_create_run_returns_run_id_and_intent(client):
    response = client.post("/api/runs", json={"query": "找北京 Agent 实习", "mode": "demo"})
    assert response.status_code == 202
    assert response.json()["run_id"]
    assert response.json()["intent"]["locations"] == ["北京"]

def test_online_mode_without_key_is_structured_error(client, monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    response = client.post("/api/runs", json={"query": "Agent intern", "mode": "online"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "search_api_error"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\pytest.exe tests\open_search\test_api.py -q`

Expected: FAIL because FastAPI app and routes do not exist.

- [ ] **Step 3: 实现最小 API**

实现 `POST /api/runs`、`GET /api/runs/{run_id}`、`GET /api/runs/{run_id}/jobs`、`GET /api/runs/{run_id}/trace`、`GET /api/runs/{run_id}/evaluation`。任务使用内存中的受限后台任务表加 artifact 目录；不引入 Celery 或多租户队列。所有响应包含 `run_id`、状态和结构化错误。

- [ ] **Step 4: 实现四视图前端**

使用原生 HTML/CSS/JavaScript，页面包含自然语言输入、在线/离线切换、解析意图确认、运行状态、轨迹、岗位详情和评测证据。前端只调用 API，不自行计算匹配分数或判断来源可信度。实现空、运行中、成功、部分成功、零结果和失败状态。

- [ ] **Step 5: 运行 API 测试和本地 smoke**

Run: `.\.venv\Scripts\pytest.exe tests\open_search\test_api.py -q`

Expected: PASS。启动：`.\.venv\Scripts\uvicorn.exe web_task_agent.open_search.api:app --reload`，浏览器打开 `http://127.0.0.1:8000/`，离线运行能看到岗位、轨迹和 artifact 链接。

- [ ] **Step 6: 提交**

```powershell
git add pyproject.toml src/web_task_agent/open_search/api.py src/web_task_agent/open_search/web/index.html tests/open_search/test_api.py
git commit -m "feat: add web agent run console"
```

### Task 7: 构建冻结真实评测与在线审计

**Files:**
- Create: `data/open-search/evaluation/queries.jsonl`
- Create: `data/open-search/evaluation/ground-truth.jsonl`
- Create: `docs/results/open-search/evaluation-report.md`
- Create: `docs/results/open-search/online-run-report.md`
- Create: `tests/open_search/test_evaluation.py`

- [ ] **Step 1: 写评测契约测试**

```python
def test_evaluation_reports_separate_metric_families(tmp_path):
    report = evaluate_frozen_queries(...)
    assert report.query_count == 20
    assert report.metric_families == ["offline_frozen", "online_audit"]
    assert report.hard_constraint_violations == 0
```

- [ ] **Step 2: 准备 20 条查询、40 个冻结页面和标注**

固定 20 条不同表达的自然语言查询；每条查询记录人工结构化意图和 Top 5 复核结果。冻结 4 个异构来源共 40 个岗位详情页，保存 URL、获取时间、内容哈希、字段标签和来源归属。困难样本、无结果样本和失败样本必须保留。

- [ ] **Step 3: 实现指标计算**

分别计算需求结构化正确率、官方/ATS 链接比例、可访问率、硬约束违反率、证据支持率、Top 5 人工相关率和重复率。报告中为每个数字写入 dataset version、provider、date 和 metric definition。

- [ ] **Step 4: 执行 3 轮在线运行**

使用 `TAVILY_API_KEY`，每轮运行固定 4 个来源/开放搜索任务，生成 `run-summary.json`、`jobs.jsonl`、`failures.jsonl`、`execution-trace.jsonl` 和 Markdown 汇总。网络失败保留原始 failure code，不替换样本或删除失败记录。

- [ ] **Step 5: 运行评测测试并提交结果**

Run: `.\.venv\Scripts\pytest.exe tests\open_search\test_evaluation.py tests\open_search -q`

Expected: PASS。执行 `python -m web_task_agent.open_search.evaluation --queries data/open-search/evaluation/queries.jsonl --output-dir docs/results/open-search` 后检查两类指标没有混合。

```powershell
git add data/open-search/evaluation docs/results/open-search tests/open_search/test_evaluation.py
git commit -m "data: add open search evaluation evidence"
```

### Task 8: 收敛发布材料并执行最终质量门禁

**Files:**
- Modify: `README.md`, `docs/interview-benchmark-story.md`, `docs/mvp-verification.md`, `docs/work-log/2026-08-24-open-search-agent.md`
- Test: `tests/test_agent_release_check.py`, `tests/open_search/test_release_contract.py`

- [ ] **Step 1: 增加发布合同测试**

```python
def test_readme_uses_open_search_metric_boundaries():
    text = Path("README.md").read_text(encoding="utf-8")
    assert "官方/可信 ATS" in text
    assert "fixture" in text
    assert "真实搜索" in text
```

- [ ] **Step 2: 压缩 README 和面试故事**

第一屏只保留项目一句话、架构图、离线命令、在线命令、证据边界和 3 条简历表述。明确“搜索摘要不是最终证据”“开放搜索不保证每次有结果”“在线指标与冻结评测分开”。

- [ ] **Step 3: 增加面试材料和工作日志**

记录真实故障：搜索 API key 缺失/超时、百度 JavaScript `undefined` 解析、页面不是岗位详情、SQLite 多连接 receipt 问题。为每个故障写触发条件、根因、修复、回归测试和面试讲法。

- [ ] **Step 4: 执行最终发布检查**

Run:

```powershell
.\.venv\Scripts\web-task-agent.exe --portfolio-demo --portfolio-demo-output-dir portfolio-artifacts
.\.venv\Scripts\web-task-agent.exe --release-check
.\.venv\Scripts\pytest.exe tests -q
git diff --check
```

Expected: portfolio demo succeeds, release check six stages pass, tests pass, and no whitespace errors exist. 在线评测若外部服务失败，必须在报告中保留失败并让 release check 与离线质量门禁保持可用。

- [ ] **Step 5: 提交最终收敛**

```powershell
git add README.md docs/interview-benchmark-story.md docs/mvp-verification.md docs/work-log/2026-08-24-open-search-agent.md tests/open_search/test_release_contract.py
git commit -m "docs: finalize open search agent portfolio"
```

## 计划自检

- 设计中的开放搜索 provider、浏览器验证、证据绑定、Hybrid policy、Web 运行台、20 条需求评测、40 个冻结页面和 3 轮在线审计均有对应任务。
- 离线 fixture 与在线真实搜索明确分离，失败结果保留并分类。
- 没有引入自动投递、多 Agent、RAG 或复杂后台队列等非目标功能。
- 所有新增模块均先写失败测试，再实现最小逻辑；已有模块只在边界确实需要时修改。
- 任务 5 的回归命令使用现有 Agent 测试文件和 `tests/open_search/` 目录，不创建无意义的聚合测试文件。
- 计划完成后使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 执行，不在本计划阶段改实现代码。
