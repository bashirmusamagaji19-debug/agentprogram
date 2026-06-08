# Web 自动任务 Agent

这是一个面向 AI 工程 / AI 应用实习的 Agent 项目。第一版目标是基于 `browser-use` 和 `LangGraph` 构建 Web 自动任务工作流，自动发现 AI 实习岗位，抽取结构化 JD，验证结果并生成 Markdown 报告。

## MVP 能力

- 通过浏览器客户端读取网页内容，已提供 deterministic fake browser 和 `browser-use` session adapter 两条边界。
- 用工作流拆分规划、浏览、抽取、验证、匹配、保存和报告生成。
- 用 SQLite 保存岗位记录和运行指标。
- 根据技能标签和简历文本生成岗位匹配分数、缺失技能和建议动作。
- 生成本地 HTML Dashboard，展示岗位、匹配分数、优先级和缺失技能。
- 运行内置 20 任务评测集，统计任务成功率、有效岗位数、平均访问页面数和失败原因分布。
- 用测试中的 fake browser 保证端到端流程可复现。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo --dashboard
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo --langgraph --dashboard
.\.venv\Scripts\web-task-agent.exe --evaluate --evaluation-count 20
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites --dashboard
.\.venv\Scripts\web-task-agent.exe --evaluate --real-smoke
```

如果 Windows PowerShell 显示中文乱码，请使用 UTF-8 终端或执行 chcp 65001 后再查看。

## 已验证的 MVP 命令

```powershell
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo --dashboard
```

该命令使用内置 demo 页面运行，不依赖真实招聘网站，适合快速展示工作流闭环。生成的 Markdown 报告会包含岗位列表、运行指标和匹配分析；`dashboards/` 下会生成可直接打开的 HTML Dashboard。当前真实 `browser-use` 路径已具备 session adapter 入口，但真实招聘网站表现仍需要单独站点评测和失败原因统计。

加上 `--langgraph` 后，主流程会通过 LangGraph 节点执行，节点包括 planner、browser、extractor、verifier、matcher 和 reporter，适合在面试中展示 Agent 工作流编排。

## 真实 browser-use adapter 状态

非 `--demo` 模式会走 `BrowserUseClient`，通过 `browser_use.BrowserSession` 打开搜索页并读取页面标题和正文。这个路径用于下一阶段真实网页接入；当前推荐演示和评测仍使用 `--demo`，因为它不依赖登录、验证码、反爬策略或外部网页结构变化。

## 已验证的评测命令

```powershell
.\.venv\Scripts\web-task-agent.exe --evaluate --evaluation-count 20
```

该命令会在 `evaluations/` 下生成评测报告。当前内置 demo 评测结果为：20/20 任务完成，任务成功率 1.00，有效岗位总数 40，平均访问页面数 2.00。报告还会输出失败原因分布；真实招聘页风格 fixture 评测可使用 `--evaluate --fixture-sites`，并可加 `--dashboard` 生成 `dashboards/evaluation-summary.html`。真实浏览器 smoke 评测可使用 `--evaluate --real-smoke`，用于观察 `browser_error`、`no_pages`、`no_extracted_jobs`、`verification_filtered` 等失败类别。
