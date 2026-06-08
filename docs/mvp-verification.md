# MVP 验证记录

## 验证命令

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo --dashboard
Get-ChildItem -Path reports -Filter *.md
Get-ChildItem -Path dashboards -Filter *.html
@'
from web_task_agent.storage import JobRepository
repo = JobRepository("agent.db")
jobs = repo.list_jobs()
print(len(jobs))
print(jobs[0].title if jobs else "no jobs")
'@ | .\.venv\Scripts\python.exe -
```

## 验证结果

- `.\.venv\Scripts\python.exe -m pytest -q` 通过，结果为 `69 passed`。
- CLI demo 成功运行，输出 `Report written to: reports\run-465dc651.md`、`Valid jobs: 2` 和 `Dashboard written to: dashboards\run-465dc651.html`。
- `reports/` 下生成 Markdown 报告，报告包含岗位列表和匹配分析。
- `dashboards/` 下生成 HTML Dashboard，展示岗位、匹配分数、优先级和缺失技能。
- SQLite 数据库 `agent.db` 中能读取到 2 条岗位记录。

## 当前限制

- 当前可演示路径使用内置 demo 页面，不依赖真实招聘网站。
- 真实 `browser-use` 网页操作仍在 `BrowserUseClient` adapter 边界之后，尚未接入生产搜索。
- 当前匹配模块基于技能标签和简历文本进行规则匹配，还不是 LLM 语义匹配。
- 当前 Dashboard 是静态 HTML 文件，不需要启动服务；交互式筛选和 20 任务评测集属于下一阶段。

## 环境备注

后续开发和验证应使用项目内 `.venv`。不要在全局 Anaconda 环境中执行 `pip install -e ".[dev]"`，因为 `browser-use` 依赖链较重，可能与全局 `streamlit`、`huggingface-hub` 等包产生版本冲突。
