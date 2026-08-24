# Public Demo Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供一个可公开访问的 Streamlit 演示入口，并保留 FastAPI 服务入口。

**Architecture:** Streamlit 只负责输入、状态和结果展示，复用 `OpenSearchPipeline` 与现有 provider。Render 配置部署 FastAPI；Streamlit Cloud 使用 `streamlit_app.py` 部署演示页面。

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, Streamlit, Tavily, Qwen/DashScope。

---

### Task 1: 演示入口

**Files:** `streamlit_app.py`, `pyproject.toml`

- [x] 增加 Demo/Online 模式选择、岗位需求输入和结果展示。
- [x] 复用现有 parser、provider 和 pipeline，不复制业务逻辑。
- [x] 配置 Streamlit 为可安装的 `demo` optional dependency。

### Task 2: 服务健康检查与云配置

**Files:** `src/web_task_agent/open_search/api.py`, `render.yaml`, `requirements.txt`

- [x] 增加 `GET /healthz`，返回 `{"status": "ok"}`。
- [x] 使用 `$PORT` 和 `0.0.0.0` 配置 Render 生产启动。
- [x] 将密钥声明为平台环境变量，不写入仓库。

### Task 3: 文档与验证

**Files:** `README.md`

- [x] 记录 Streamlit Cloud 和 Render 部署步骤及单实例限制。
- [x] 运行 `python -m pytest`、Python 编译检查、健康检查请求。
- [x] 实际启动 Streamlit 无头服务并验证 HTTP 200。
