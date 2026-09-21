# Streamlit UI Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Streamlit 运行台重构为高对比度、双栏、岗位优先的沉稳指挥台，同时保持全部业务能力不变。

**Architecture:** 继续使用单个 Streamlit 页面和现有业务函数，只调整 `streamlit_app.py` 的主题常量、CSS 和 `main()` 布局。左栏承载现有配置顺序，右栏承载结果视图；纯函数和 CSS 可独立测试。

**Tech Stack:** Python 3.11+, Streamlit, pytest, Streamlit AppTest

---

## Task 1: 建立可测试的主题契约

**Files:**
- Modify: `src/web_task_agent/streamlit_app.py`
- Test: `tests/test_streamlit_app.py`

- [ ] **Step 1: 写失败测试**

增加 `THEME_COLORS` 导入并断言关键颜色对对比度不低于 4.5，CSS 不再包含全局侧栏覆盖。

- [ ] **Step 2: 运行失败测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_streamlit_app.py -q`

Expected: FAIL because `THEME_COLORS` does not exist.

- [ ] **Step 3: 实现主题常量和高对比样式**

在 `streamlit_app.py` 增加颜色常量，替换旧 CSS 变量和侧栏选择器。

- [ ] **Step 4: 运行测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_streamlit_app.py -q`

Expected: PASS.

- [ ] **Step 5: 提交**

```powershell
git add src/web_task_agent/streamlit_app.py tests/test_streamlit_app.py
git commit -m "feat: establish high-contrast Streamlit theme"
```

## Task 2: 重构为双栏工作台

**Files:**
- Modify: `src/web_task_agent/streamlit_app.py`
- Test: `tests/test_persistent_streamlit.py`

- [ ] **Step 1: 写空状态失败测试**

用 AppTest 断言无历史记录的首次页面同时包含“配置任务”“运行结果”和空状态说明。

- [ ] **Step 2: 运行失败测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_persistent_streamlit.py::test_streamlit_app_renders_dual_column_workspace -q`

Expected: FAIL because the new workspace text does not exist.

- [ ] **Step 3: 实现双栏工作台**

把侧栏控件移入主区左栏，保留原字段顺序；右栏渲染岗位优先结果或空状态。

- [ ] **Step 4: 运行测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_persistent_streamlit.py -q`

Expected: PASS.

- [ ] **Step 5: 提交**

```powershell
git add src/web_task_agent/streamlit_app.py tests/test_persistent_streamlit.py
git commit -m "feat: rebuild Streamlit as a dual-column workspace"
```

## Task 3: 全量验证与预览

**Files:**
- Modify: `docs/project-story.md` only if the UI boundary description changes.

- [ ] **Step 1: 运行完整测试**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: all tests pass.

- [ ] **Step 2: 运行 Ruff**

Run: `.\.venv\Scripts\python.exe -m ruff check src tests scripts`

Expected: `All checks passed!`

- [ ] **Step 3: 启动本地 Streamlit**

Run on a free port with a temporary persistence root.

- [ ] **Step 4: 浏览器验收**

检查桌面与 390px 视口、文字对比度、无重叠和关键操作可用性。

- [ ] **Step 5: 提交文档或视觉修正**

```powershell
git add <changed-files>
git commit -m "docs: record Streamlit workspace refresh"
```
