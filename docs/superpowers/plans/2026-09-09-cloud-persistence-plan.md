# 云端持久化与多用户演进实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 先让 Streamlit 云端在实例重启后保留运行记录和下载产物，再按并发和用户隔离需求演进为 API、队列和 Worker 架构。

> **修订(2026-09-09,评审后)**:
> 1. **owner_id 认证来源补齐** — Community Cloud 无用户系统,采用**单共享 owner + 访问口令**:Secrets 配 `APP_PASSCODE`,首次访问输入,`owner_id` 固定为 `"demo"`;越权测试退化为"无口令者查不到"
> 2. **Task 4(阶段 B)推迟** — Community Cloud 无法跑常驻 Worker,且暂无真实多用户需求;触发条件:出现真实多用户/长任务需求时重启此计划
> 3. **psycopg/对象存储 SDK 必须 lazy import** — 不配数据库的本地环境不强制装
> 4. Task 3 验收先本地仿真(File adapter + 新进程连同一目录验证重启恢复),真部署验收在其后
> 5. 笔误修正:`\.venv` → `.venv`

**Architecture:** 阶段 A 在现有 Streamlit 薄层下增加 `RunStore`、`ArtifactStore` 抽象，使用 PostgreSQL-compatible 数据库存运行元数据和 S3-compatible 对象存储保存产物；本地开发保留 SQLite/filesystem adapter。阶段 B 将同步执行移动到 FastAPI + Redis 队列 + Worker，Streamlit 只创建任务、轮询状态和获取签名下载 URL。现有 `WebTaskWorkflow` 不改业务语义。

**Tech Stack:** Python 3.11+, Pydantic, psycopg 3, PostgreSQL, S3-compatible storage (R2/S3/Supabase Storage), Streamlit, FastAPI, Redis queue, pytest.

---

## 文件结构

- Create: `src/web_task_agent/run_store.py` - 运行记录协议、本地 adapter、Postgres adapter。
- Create: `src/web_task_agent/artifact_store.py` - 文件系统和对象存储 adapter、签名下载接口。
- Create: `src/web_task_agent/persistence_schema.sql` - runs/artifacts/diagnostics/jobs/matches 表与索引。
- Modify: `src/web_task_agent/streamlit_runner.py` - 注入存储 adapter，完成后登记 run 和产物。
- Modify: `src/web_task_agent/streamlit_app.py` - 从持久化记录恢复历史 run，显示状态和下载链接。
- Create: `tests/test_run_store.py`, `tests/test_artifact_store.py`, `tests/test_persistent_streamlit.py`。
- Create: `src/web_task_agent/api.py` - 阶段 B 的任务 API。
- Create: `src/web_task_agent/worker.py` - 阶段 B 的队列消费和工作流执行。
- Create: `tests/test_api.py`, `tests/test_worker.py`。
- Modify: `requirements.txt`, `pyproject.toml`, `README.md`, `.env.example` - 可选云端依赖、配置和部署说明。
- Create: `docs/work-log/2026-09-09-cloud-persistence.md` - 实际部署和重启验证记录。

## Task 1: 设计持久化数据模型并保留本地 adapter

**Files:**
- Create: `src/web_task_agent/run_store.py`
- Create: `src/web_task_agent/artifact_store.py`
- Create: `src/web_task_agent/persistence_schema.sql`
- Create: `tests/test_run_store.py`, `tests/test_artifact_store.py`

- [ ] **Step 1: 写本地 adapter 的失败测试**

覆盖 run 的 `queued/running/succeeded/partial/failed` 状态、按 owner 查询、幂等创建、状态转移、artifact 元数据登记、缺失文件拒绝下载和过期记录清理。

- [ ] **Step 2: 定义最小数据模型**

`RunRecord` 包含 `run_id`、`owner_id`、`status`、`data_mode`、请求摘要、metrics、错误摘要、时间戳和 retention deadline；不得包含 API key 或原始简历正文。`ArtifactRecord` 包含 artifact 类型、对象 key、mime、大小、sha256、owner_id 和过期时间。

- [ ] **Step 3: 实现 `FileRunStore` 和 `FileArtifactStore`**

开发环境继续写本地文件，但所有读操作必须按 `owner_id + run_id` 检查；保留现有 `UiRunResult.artifacts` 白名单，不允许用用户提交的路径读取任意文件。

- [ ] **Step 4: 实现 Postgres schema 和 adapter**

使用参数化 SQL，给 `runs(owner_id, created_at)`、`artifacts(owner_id, run_id)` 建索引；状态更新使用 `WHERE run_id = ? AND owner_id = ?` 等价条件，避免越权和竞态覆盖。数据库连接通过 `DATABASE_URL`，不在代码或日志中打印完整 URL。

- [ ] **Step 5: 运行 adapter 测试**

本地默认跑 File adapter；配置 `TEST_DATABASE_URL` 时再跑 Postgres 集成测试。没有数据库服务时测试必须明确跳过，不得伪装成通过。

- [ ] **Step 6: 提交存储边界**

提交信息：`feat: add persistent run and artifact storage boundary`。

## Task 2: 接入阶段 A Streamlit 持久化

**Files:**
- Modify: `src/web_task_agent/streamlit_runner.py`
- Modify: `src/web_task_agent/streamlit_app.py`
- Create: `tests/test_persistent_streamlit.py`

- [ ] **Step 1: 写重启恢复测试**

用同一 `FileRunStore` 模拟两个 Streamlit session：session A 创建并完成 run，session B 不依赖 `st.session_state` 也能按 owner 查询结果和 artifact 元数据。

- [ ] **Step 2: 给 `run_ui_request()` 增加依赖注入**

增加 `run_store`、`artifact_store` 和 `owner_id` 参数；未传入时使用现有本地路径，保证当前 CLI/测试兼容。业务工作流仍由原函数调用。

- [ ] **Step 3: 完成产物上传和登记**

运行成功后按固定 artifact key 上传 JSON/Markdown/HTML/action-plan，保存 sha256、mime、字节数和相对显示名；数据库只存对象 key，不存服务器绝对路径。

- [ ] **Step 4: 增加历史运行列表**

Streamlit 侧栏显示当前 owner 最近 10 次运行；刷新页面通过 `RunStore` 恢复，不再把 `latest_ui_result` 作为唯一来源。Demo 未配置数据库时继续使用本地 adapter。

- [ ] **Step 5: 加入保留和删除策略**

默认保留 7 天，定期清理过期 artifact；运行记录保留最小审计字段。原始简历默认不持久化，若报告间接包含用户输入，必须在上传前做脱敏策略或明确 retention 文档。

- [ ] **Step 6: 验证重启和越权场景**

运行 `.venv\Scripts\python.exe -m pytest tests/test_persistent_streamlit.py -q`；手动部署后创建 run、重启实例、再次打开页面，确认历史记录和下载仍可用。

- [ ] **Step 7: 提交阶段 A**

提交信息：`feat: persist Streamlit runs and artifacts`。

## Task 3: 阶段 A 云端部署验收

**Files:**
- Modify: `requirements.txt`
- Modify: `.env.example`
- Modify: `README.md`
- Create: `docs/work-log/2026-09-09-cloud-persistence.md`

- [ ] **Step 1: 添加可选依赖和配置说明**

把 `psycopg` 和对象存储 SDK 放在云端依赖中；配置只使用 `DATABASE_URL`、`OBJECT_STORAGE_ENDPOINT`、`OBJECT_STORAGE_BUCKET`、`OBJECT_STORAGE_ACCESS_KEY`、`OBJECT_STORAGE_SECRET_KEY`，密钥只进平台 Secrets。

- [ ] **Step 2: 部署共享数据库和对象存储**

创建最小权限数据库用户和私有 bucket，启用生命周期清理；不允许公开 bucket，不允许把对象 key 当作公开 URL。

- [ ] **Step 3: 做冷启动/重启验收**

记录部署版本、run_id、数据库记录、artifact sha256；重启应用后重新查询同一 run，并检查下载内容哈希一致。

- [ ] **Step 4: 做越权和敏感信息验收**

用两个测试 owner 互相访问 run/artifact，预期均返回“未找到”；扫描日志和产物，确认没有 key、Authorization、完整简历正文和绝对服务器路径。

- [ ] **Step 5: 更新边界文档并提交**

提交信息：`docs: document persistent cloud deployment verification`。

## Task 4: 阶段 B API、队列和 Worker（**已推迟** — Community Cloud 无法跑常驻 Worker;暂无真实多用户需求。触发条件:出现真实多用户/长任务需求时重启此计划。以下原始内容保留作参考）

**Files:**
- Create: `src/web_task_agent/api.py`
- Create: `src/web_task_agent/worker.py`
- Create: `tests/test_api.py`, `tests/test_worker.py`
- Modify: `streamlit_app.py`, `requirements.txt`, `README.md`

- [ ] **Step 1: 定义任务 API 契约**

`POST /runs` 返回 `202` 和 `run_id`；`GET /runs/{run_id}` 返回状态和安全摘要；`GET /runs/{run_id}/artifacts` 返回签名下载 URL；所有接口要求 owner 身份，并在查询层强制 owner 过滤。

- [ ] **Step 2: 写 API 失败测试**

覆盖未授权、越权访问、重复幂等键、无效请求、状态查询、任务取消和不存在 artifact；测试不得依赖真实队列或外部网站。

- [ ] **Step 3: 实现队列提交和 Worker**

API 只写 queued 状态并投递任务；Worker 更新 running/partial/succeeded/failed，调用现有 `run_ui_request()` 核心执行函数，设置最大运行时间、重试次数和取消检查。

- [ ] **Step 4: 改造 Streamlit 为轮询客户端**

提交后显示 run 状态，按退避间隔查询 API；刷新页面可继续查看，不在 Streamlit 进程内执行长任务。下载只使用签名 URL。

- [ ] **Step 5: 加入监控和限流**

记录任务吞吐、成功/部分成功/失败数、运行时长、队列等待时间、外部页面失败类别和存储错误；对 owner 设置并发数、每日运行数、单次目标岗位数上限。

- [ ] **Step 6: 阶段 B 验收**

使用两个 owner 并发提交任务，验证隔离、幂等、失败重试、取消和超时；监控中能定位到 run_id，但不出现密钥或简历正文。

- [ ] **Step 7: 提交生产化边界**

提交信息：`feat: add queued multi-user run execution`。

## 完成门槛

- 阶段 A：重启后历史 run 和 artifact 可恢复；文件与数据库不再是唯一真相；下载通过 owner 和白名单校验。
- 阶段 B：长任务不阻塞 Web 请求；两个 owner 不能互读；任务状态最终一致；失败、重试、取消和超时都有可审计记录。
- Streamlit Community Cloud 若无法保证后台进程、数据库网络或对象存储配置，则保持阶段 A/演示定位，不宣称生产级多用户服务。
