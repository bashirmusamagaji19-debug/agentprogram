# Online Acceptance Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供一条可复现的真实在线验收命令，并让云端 CI 验证 Docker 镜像始终可构建。

**Architecture:** `online_smoke.py` 只负责编排多条查询和汇总报告，单条查询仍走现有 parser/provider/pipeline。CI 的 Docker job 与 Python quality job 分离，使构建失败与业务测试失败容易定位。

**Tech Stack:** Python 3.11+, asyncio, Pydantic artifacts, Tavily, pytest, GitHub Actions, Docker.

---

### Task 1: Online smoke report

**Files:**
- Create: `src/web_task_agent/open_search/online_smoke.py`
- Create: `tests/open_search/test_online_smoke.py`

- [ ] **Step 1: Write failing report tests**

测试注入 fixture provider，断言多个查询分别写入 artifact，JSON/Markdown 汇总包含 `online`、provider、岗位数量和失败分类。

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/open_search/test_online_smoke.py -q`
Expected: FAIL because `web_task_agent.open_search.online_smoke` does not exist.

- [ ] **Step 3: Implement minimal report runner and CLI**

实现 `run_online_smoke(...)`、Markdown 渲染、原子写文件、参数解析和 `main()`；provider 通过参数注入，CLI 默认从环境创建 Tavily provider。

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/open_search/test_online_smoke.py -q`
Expected: all tests pass.

### Task 2: Docker build gate

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/open_search/test_ci_contract.py`

- [ ] **Step 1: Add a failing CI contract test**

断言 workflow 存在独立 `docker-build` job，并执行 `docker build`，且该步骤不引用 Tavily/DashScope secret。

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/open_search/test_ci_contract.py -q`
Expected: FAIL because the Docker job is absent.

- [ ] **Step 3: Add the minimal workflow job**

在 Ubuntu runner checkout 后执行 `docker build --tag web-task-agent:ci .`。

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/open_search/test_ci_contract.py -q`
Expected: all tests pass.

### Task 3: Documentation and release evidence

**Files:**
- Modify: `README.md`
- Create: `docs/work-log/2026-08-26-online-acceptance-automation.md`

- [ ] **Step 1: Document the command and boundaries**

记录设置 `TAVILY_API_KEY` 后运行 `python -m web_task_agent.open_search.online_smoke`，以及 JSON/Markdown/artifact 输出位置；明确未配置 key 时不能形成真实在线证据。

- [ ] **Step 2: Run release verification**

Run: `python -m ruff check src/web_task_agent/open_search tests/open_search streamlit_app.py`

Run: `python -m pytest -p no:cacheprovider --cov=web_task_agent --cov-report=term-missing --cov-fail-under=70`

Run without a key: `python -m web_task_agent.open_search.online_smoke`

Expected: Ruff and tests succeed; the CLI exits non-zero with a missing-key message and never prints a secret.

- [ ] **Step 3: Commit and push**

Commit the focused changes, push `feature/open-web-job-agent`, and wait for both GitHub Actions jobs to succeed.
