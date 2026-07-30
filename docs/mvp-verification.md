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
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --seed-url "https://example.com/jobs/unstructured-ai-agent-intern" --seed-url "https://example.com/jobs/ai-engineering-intern" --json-output evaluations\seed-comparison.json
.\.venv\Scripts\web-task-agent.exe --evaluate --real-site-sample --evaluation-count 2 --json-output evaluations\real-site.json
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --real-site-sample --evaluation-count 2 --json-output evaluations\real-site-comparison.json
.\.venv\Scripts\web-task-agent.exe --evaluate --real-site-sample --evaluation-count 4 --llm-extractor-provider deepseek --json-output evaluations\real-site-4.json
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo --dashboard --action-plan --json-output outputs\result.json
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo --langgraph --dashboard
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --target-count 2 --skill Python --resume-text "Built LangGraph browser agents with LLM evaluation loops." --demo --dashboard
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/ai-engineering-intern" --demo --target-count 1 --json-output outputs\seed-demo.json
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/unstructured-ai-agent-intern" --demo --target-count 1 --llm-extractor-demo --json-output outputs\unstructured-llm-demo.json --dashboard
$env:DEEPSEEK_API_KEY="..."
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/unstructured-ai-agent-intern" --demo --target-count 1 --llm-extractor-provider deepseek --llm-extractor-model deepseek-v4-flash --json-output outputs\deepseek-llm-demo.json
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
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --demo --hybrid-agent --target-count 1 --agent-max-steps 8 --db-path ":memory:" --json-output outputs\hybrid-agent-demo.json
.\.venv\Scripts\python.exe -m web_task_agent.agent_evaluation --output-dir docs\results
.\.venv\Scripts\python.exe -m ruff check src\web_task_agent\agent_*.py src\web_task_agent\search_discovery.py tests\test_agent_*.py tests\test_search_discovery.py
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

- 2026-07-29 Hybrid Agent 聚焦验证为 `44 passed`，Ruff 聚焦检查为 `All checks passed`。
- 本机全量 pytest 收集 253 项，其中 `169 passed, 84 errors`；84 项均在 pytest 创建 `tmp_path` 时被 Windows 沙箱以 `WinError 5` 拒绝，未观察到业务断言失败。Python 3.11 GitHub Actions 负责运行全量套件与 70% coverage 门禁。
- 稳定 Hybrid demo 终止状态为 `completed / target_reached`，动作序列为 `search_jobs -> open_page -> extract_text -> verify_job -> finish`，工具成功率 1.0。
- `hybrid-agent-deterministic-v1` 生成 10 个合成确定性场景证据：8/10 达到业务目标、10/10 正常终止、工具成功率 88.46%；该结果验证编排与恢复，不代表真实网站抽取泛化。
- `hybrid-agent-planner-controlled-v1` 在相同 5 个受控 runtime 场景中完成真实 Planner 对照：deterministic、DeepSeek、Qwen 均为 4/5 目标完成和 5/5 正常终止；DeepSeek 为 15 次调用 / 5 次 fallback / 5518 tokens，Qwen 为 16 次调用 / 0 fallback / 5077 tokens。该结果使用真实模型 API，但不代表真实网站泛化。
- Planner benchmark JSON/Markdown 记录每个 case 的动作序列与决策来源，不保存 API key、Authorization header、prompt、响应正文、简历或页面正文；敏感字段扫描无匹配。
- CLI 版本命令成功运行，输出 `web-task-agent 0.1.0`。
- CLI 环境自检成功运行，输出 Python 路径、虚拟环境状态、依赖 import 状态和输出目录可写性。
- fixture URL 列表命令成功运行，输出内置 Greenhouse/Lever 风格演示链接。
- demo script 命令成功运行，输出 8 条面试现场可复制命令，包含一键生成 Dashboard、行动计划和 JSON 的闭环命令、LangGraph 编排对比命令，以及运行历史查询命令。
- LLM extractor 对比命令成功运行，输出 `baseline: 0/1` 和 `llm-demo: 1/1`，并生成 `evaluations\llm-comparison.json`。
- 多 seed URL LLM extractor 对比命令成功运行，输出 `baseline: 1/2` 和 `llm-demo: 2/2`，并生成 `evaluations\seed-comparison.json` 与 `evaluations/llm-extractor-comparison.md`。
- 真实站点样本模式已接入 `--evaluate` 和 `--compare-llm-extractor`，可对固定真实 URL 样本做同批对比，并通过 HTTP loader 读取正文。
- 真实站点样本 8 条正式评测已成功运行，输出 DeepSeek 88% (7/8) 完成率，规则抽取仅 25% (2/8)。
- 评测 JSON 和 report 现在都会写出被 verifier 过滤的具体岗位、公司和原因，便于复盘 benchmark 差异。
- CLI demo 成功运行，输出 `Report written to: reports\run-*.md`、`Valid jobs: 2`、`Action plan written to: action-plans\...`、`Top action gaps: ...`、`JSON output written to: outputs\result.json` 和 `Dashboard written to: dashboards\run-*.html`。
- LangGraph demo 成功运行，输出 `LangGraph workflow: enabled`、`Valid jobs: 2` 和 dashboard 路径；JSON 中 `metadata.orchestration_mode` 为 `langgraph`。
- 带简历文本的 demo 成功运行，输出 `Valid jobs: 2`，并在报告中将简历内容作为匹配信号。
- JSON 导出 demo 成功运行，输出 `JSON output written to: outputs\result.json`。
- 行动计划 demo 成功运行，输出 `Action plan written to: action-plans\...` 和 `Top action gaps: ...`；Markdown 包含优先投递岗位、技能补强顺序、补强项目任务、简历项目改写要点、7 天执行节奏，以及技术栈体验与面试说法。
- seed URL demo 成功运行，输出 `Valid jobs: 1` 和 `JSON output written to: outputs\seed-demo.json`，说明可跳过搜索并直接打开指定 JD。
- deterministic LLM extractor demo 成功运行，输出 `LLM extractor demo: enabled`、`Valid jobs: 1` 和 `JSON output written to: outputs\unstructured-llm-demo.json`；JSON 中 `metadata.extractor_mode` 为 `llm-demo`，岗位为 `AI Agent Intern / Example Robotics`。
- DeepSeek/Qwen provider 边界已接入 CLI，使用 `--llm-extractor-provider deepseek|qwen` 启用；测试通过 fake transport 验证 OpenAI-compatible 请求、JSON 输出解析和 provider/model metadata，不依赖真实 API key。
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
- `reports/` 下生成 Markdown 报告，报告包含岗位列表、匹配分析、行动计划链接和 Dashboard 链接；相关产物使用相对 Markdown 链接。
- Markdown 报告包含 `面试讲述要点`，把 BrowserClient 边界、编排模式、Agent 执行轨迹和评测闭环转化为可直接讲的项目叙事。
- Markdown 报告包含 `Agent 执行轨迹`，展示 planner、browser、extractor、verifier、matcher、reporter 节点摘要。
- Markdown 报告和 Dashboard 展示编排模式，区分 sequential 与 LangGraph 路径。
- `action-plans/` 下生成 Markdown 行动计划，报告投递优先级、技能缺口、项目补强任务、简历项目改写要点、7 天执行节奏和技术栈体验与面试说法。
- `dashboards/` 下生成 HTML Dashboard，展示岗位、匹配分数、优先级、缺失技能、技能缺口汇总、输入轨迹、Agent 执行轨迹和行动计划等相关产物链接。
- 岗位 Dashboard 支持文本搜索、优先级筛选和匹配分数排序。
- `evaluations/evaluation-report.md` 记录任务总数、完成任务数、任务成功率、有效岗位总数和平均访问页面数。
- SQLite 数据库 `agent.db` 中能读取到 2 条岗位记录。
- `outputs/result.json` 能读取到用户输入、岗位、匹配结果、运行指标、报告路径、`metadata.orchestration_mode` 和 `metadata.execution_trace`；与 `--action-plan` / `--dashboard` 同用时包含 `metadata.action_plan_path`、`metadata.dashboard_path` 和 `metadata.top_action_gaps`。

## HITL checkpoint 验证（2026-07-30）

- 使用 `langgraph-checkpoint-sqlite` 持久化 LangGraph `interrupt`，可由新 runtime 使用同一
  `thread_id` 跨连接恢复。
- 暂停发生在 `save_results` 之前；暂停状态的业务数据库为空，审批本身不消耗工具步数。
- approve 路径执行原保存动作并完成为 `target_reached`；reject 路径不调用保存工具，以
  `human_denied` 结束。
- `approval_id` 同时作为 repository receipt 幂等键，重复重放不会覆盖首次结果或增加第二次
  可见副作用。
- `LANGGRAPH_STRICT_MSGPACK=true` 下实际运行三场景 benchmark 成功，没有未注册类型警告。
- `docs/results/hitl-checkpoint/` 中的 `hybrid-agent-hitl-v1` 记录 3/3 暂停、拒绝副作用 0、
  重复副作用 0；它是 deterministic fixture 证据，不衡量真实站点抽取。
- 历史 `hybrid-agent-planner-controlled-v1` 文件保持不变。动作序列改变后的新 planner 运行使用
  `hybrid-agent-planner-controlled-v2` 和 `docs/results/planner-benchmark-v2/`，未实际重跑的
  DeepSeek/Qwen 指标不会从 v1 复制。
- 该功能不需要 GPU、云服务器、微调或人工数据标注。

## 当前限制

- 当前可演示路径使用内置 demo 页面，不依赖真实招聘网站。
- 真实 `browser-use` session adapter 已接入，可通过 `BrowserUseClient` 打开页面并读取标题和正文；当前 demo/evaluation 仍使用内置页面保证可复现。
- 匹配模块支持规则优先和可选 LLM 语义匹配；语义质量仍需要人工标注的多画像评测。
- Dashboard 是静态 HTML 文件，不需要启动服务，已支持搜索、筛选和排序。
- 当前评测集使用内置 demo 页面，因此指标代表确定性 MVP 闭环，不代表真实招聘网站表现。

## 环境备注

后续开发和验证应使用项目内 `.venv`。不要在全局 Anaconda 环境中执行 `pip install -e ".[dev]"`，因为 `browser-use` 依赖链较重，可能与全局 `streamlit`、`huggingface-hub` 等包产生版本冲突。
