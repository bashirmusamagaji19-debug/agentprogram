# MVP 验证记录

## 验证命令

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo --dashboard
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --target-count 1
.\.venv\Scripts\web-task-agent.exe --evaluate --evaluation-count 20
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites --dashboard
.\.venv\Scripts\web-task-agent.exe --evaluate --real-smoke
Get-ChildItem -Path reports -Filter *.md
Get-ChildItem -Path dashboards -Filter *.html
Get-Content -LiteralPath evaluations\evaluation-report.md -Encoding UTF8
@'
from web_task_agent.storage import JobRepository
repo = JobRepository("agent.db")
jobs = repo.list_jobs()
print(len(jobs))
print(jobs[0].title if jobs else "no jobs")
'@ | .\.venv\Scripts\python.exe -
```

## 验证结果

- `.\.venv\Scripts\python.exe -m pytest -q` 通过，结果为 `89 passed`。
- CLI demo 成功运行，输出 `Report written to: reports\run-*.md`、`Valid jobs: 2` 和 `Dashboard written to: dashboards\run-*.html`。
- 20 任务评测成功运行，输出 `Task success rate: 1.00` 和 `Completed tasks: 20/20`，并在报告中生成失败原因分布表。
- 公开招聘页 fixture 评测成功运行，输出 `Completed tasks: 2/2`，覆盖 Greenhouse/Lever 风格自然语言招聘页抽取。
- 评测摘要 Dashboard 成功生成，输出 `Evaluation dashboard written to: dashboards\evaluation-summary.html`。
- 非 demo 的 `BrowserUseClient` 本地 session adapter 成功运行，输出 `Report written to: reports\run-*.md` 和 `Valid jobs: 0`；该结果说明真实浏览器入口可执行，但搜索页尚未转化为招聘站点 JD 抽取。`--evaluate --real-smoke` 可批量运行真实浏览器 smoke task，并把失败归类为 `browser_error`、`no_pages`、`no_extracted_jobs` 或 `verification_filtered`。
- `reports/` 下生成 Markdown 报告，报告包含岗位列表和匹配分析。
- `dashboards/` 下生成 HTML Dashboard，展示岗位、匹配分数、优先级和缺失技能。
- `evaluations/evaluation-report.md` 记录任务总数、完成任务数、任务成功率、有效岗位总数和平均访问页面数。
- SQLite 数据库 `agent.db` 中能读取到 2 条岗位记录。

## 当前限制

- 当前可演示路径使用内置 demo 页面，不依赖真实招聘网站。
- 真实 `browser-use` session adapter 已接入，可通过 `BrowserUseClient` 打开页面并读取标题和正文；当前 demo/evaluation 仍使用内置页面保证可复现。
- 当前匹配模块基于技能标签和简历文本进行规则匹配，还不是 LLM 语义匹配。
- 当前 Dashboard 是静态 HTML 文件，不需要启动服务；交互式筛选属于下一阶段。
- 当前评测集使用内置 demo 页面，因此指标代表确定性 MVP 闭环，不代表真实招聘网站表现。

## 环境备注

后续开发和验证应使用项目内 `.venv`。不要在全局 Anaconda 环境中执行 `pip install -e ".[dev]"`，因为 `browser-use` 依赖链较重，可能与全局 `streamlit`、`huggingface-hub` 等包产生版本冲突。
