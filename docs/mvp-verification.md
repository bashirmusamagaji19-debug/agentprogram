# MVP 验证记录

## 验证命令

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\web-task-agent.exe --version
.\.venv\Scripts\web-task-agent.exe --doctor
.\.venv\Scripts\web-task-agent.exe --list-fixture-urls
.\.venv\Scripts\web-task-agent.exe --print-demo-script
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --json-output evaluations\llm-comparison.json
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo --dashboard
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo --langgraph --dashboard
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --target-count 2 --skill Python --resume-text "Built LangGraph browser agents with LLM evaluation loops." --demo --dashboard
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --target-count 2 --skill Python --demo --json-output outputs\result.json
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --target-count 2 --skill Python --demo --action-plan
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/ai-engineering-intern" --demo --target-count 1 --json-output outputs\seed-demo.json
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/unstructured-ai-agent-intern" --demo --target-count 1 --llm-extractor-demo --json-output outputs\unstructured-llm-demo.json --dashboard
.\.venv\Scripts\web-task-agent.exe --history
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --target-count 1
.\.venv\Scripts\web-task-agent.exe --evaluate --evaluation-count 20
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites --json-output evaluations\fixture-result.json
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites --seed-url "https://boards.greenhouse.io/example/jobs/ai-agent-intern" --json-output evaluations\seed-url-result.json
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites --seed-url "https://boards.greenhouse.io/example/jobs/missing" --json-output evaluations\missing-seed-url-result.json
.\.venv\Scripts\web-task-agent.exe --evaluate --seed-url "https://example.com/jobs/unstructured-ai-agent-intern" --llm-extractor-demo --json-output evaluations\unstructured-llm-result.json
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites --dashboard
.\.venv\Scripts\web-task-agent.exe --evaluate --real-smoke
.\.venv\Scripts\web-task-agent.exe --export-graph
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

- `.\.venv\Scripts\python.exe -m pytest -q` 通过，结果为 `134 passed`。
- CLI 版本命令成功运行，输出 `web-task-agent 0.1.0`。
- CLI 环境自检成功运行，输出 Python 路径、依赖 import 状态和输出目录可写性。
- fixture URL 列表命令成功运行，输出内置 Greenhouse/Lever 风格演示链接。
- demo script 命令成功运行，输出 7 条面试现场可复制命令，包含行动计划生成路径。
- LLM extractor 对比命令成功运行，输出 `baseline: 0/1` 和 `llm-demo: 1/1`，并生成 `evaluations\llm-comparison.json`。
- CLI demo 成功运行，输出 `Report written to: reports\run-*.md`、`Valid jobs: 2` 和 `Dashboard written to: dashboards\run-*.html`。
- LangGraph demo 成功运行，输出 `LangGraph workflow: enabled`、`Valid jobs: 2` 和 dashboard 路径。
- 带简历文本的 demo 成功运行，输出 `Valid jobs: 2`，并在报告中将简历内容作为匹配信号。
- JSON 导出 demo 成功运行，输出 `JSON output written to: outputs\result.json`。
- 行动计划 demo 成功运行，输出 `Action plan written to: action-plans\...`；Markdown 包含优先投递岗位、技能补强顺序和补强项目任务。
- seed URL demo 成功运行，输出 `Valid jobs: 1` 和 `JSON output written to: outputs\seed-demo.json`，说明可跳过搜索并直接打开指定 JD。
- deterministic LLM extractor demo 成功运行，输出 `LLM extractor demo: enabled`、`Valid jobs: 1` 和 `JSON output written to: outputs\unstructured-llm-demo.json`；JSON 中 `metadata.extractor_mode` 为 `llm-demo`，岗位为 `AI Agent Intern / Example Robotics`。
- deterministic LLM extractor evaluation 成功运行，输出 `LLM extractor demo: enabled`、`Completed tasks: 1/1` 和 `Evaluation JSON written to: evaluations\unstructured-llm-result.json`。
- seed URL Dashboard 成功运行，生成的 HTML 包含 `Input Trace`、`Seed URL mode` 和指定 JD 链接。
- 搜索模式 Dashboard 成功运行，生成的 HTML 包含 `Input Trace`、`Search query mode` 和 `AI intern Remote`。
- 缺失 seed URL Dashboard 成功运行，生成的 HTML 包含 `URL Errors`、缺失 URL 和 `ValueError`。
- 运行历史查询成功运行，输出 `Recent runs` 和最近 run 的 `valid_jobs` 等指标。
- 20 任务评测成功运行，输出 `Task success rate: 1.00` 和 `Completed tasks: 20/20`，并在报告中生成失败原因分布表。
- 公开招聘页 fixture 评测成功运行，输出 `Completed tasks: 2/2`，覆盖 Greenhouse/Lever 风格自然语言招聘页抽取。
- 评测 JSON 导出成功运行，输出 `Evaluation JSON written to: evaluations\fixture-result.json`。
- seed URL fixture 评测成功运行，输出 `Completed tasks: 1/1` 和 `Evaluation JSON written to: evaluations\seed-url-result.json`。
- 缺失 seed URL fixture 评测成功归类失败，输出 `Completed tasks: 0/1`；`evaluations\missing-seed-url-result.json` 中的 `failure_details` 包含具体 URL 和 `ValueError` 类型。
- 评测摘要 Dashboard 成功生成，输出 `Evaluation dashboard written to: dashboards\evaluation-summary.html`。
- LangGraph 工作流图成功导出，输出 `Graph written to: docs\agent-workflow-graph.md`。
- 非 demo 的 `BrowserUseClient` 本地 session adapter 成功运行，输出 `Report written to: reports\run-*.md` 和 `Valid jobs: 0`；该结果说明真实浏览器入口可执行，但搜索页尚未转化为招聘站点 JD 抽取。`--evaluate --real-smoke` 可批量运行真实浏览器 smoke task，并把失败归类为 `browser_error`、`no_pages`、`no_extracted_jobs` 或 `verification_filtered`。
- `reports/` 下生成 Markdown 报告，报告包含岗位列表和匹配分析。
- `action-plans/` 下生成 Markdown 行动计划，报告投递优先级、技能缺口和项目补强任务。
- `dashboards/` 下生成 HTML Dashboard，展示岗位、匹配分数、优先级、缺失技能、技能缺口汇总和输入轨迹。
- 岗位 Dashboard 支持文本搜索、优先级筛选和匹配分数排序。
- `evaluations/evaluation-report.md` 记录任务总数、完成任务数、任务成功率、有效岗位总数和平均访问页面数。
- SQLite 数据库 `agent.db` 中能读取到 2 条岗位记录。
- `outputs/result.json` 能读取到用户输入、岗位、匹配结果、运行指标和报告路径。

## 当前限制

- 当前可演示路径使用内置 demo 页面，不依赖真实招聘网站。
- 真实 `browser-use` session adapter 已接入，可通过 `BrowserUseClient` 打开页面并读取标题和正文；当前 demo/evaluation 仍使用内置页面保证可复现。
- 当前匹配模块基于技能标签和简历文本进行规则匹配，还不是 LLM 语义匹配。
- 当前 Dashboard 是静态 HTML 文件，不需要启动服务；交互式筛选属于下一阶段。
- 当前评测集使用内置 demo 页面，因此指标代表确定性 MVP 闭环，不代表真实招聘网站表现。

## 环境备注

后续开发和验证应使用项目内 `.venv`。不要在全局 Anaconda 环境中执行 `pip install -e ".[dev]"`，因为 `browser-use` 依赖链较重，可能与全局 `streamlit`、`huggingface-hub` 等包产生版本冲突。
