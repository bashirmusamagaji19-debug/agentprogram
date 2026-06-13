# Web 自动任务 Agent

这是一个面向 AI 工程 / AI 应用实习的 Agent 项目。第一版目标是基于 `browser-use` 和 `LangGraph` 构建 Web 自动任务工作流，自动发现 AI 实习岗位，抽取结构化 JD，验证结果并生成 Markdown 报告。

## MVP 能力

- 通过浏览器客户端读取网页内容，已提供 deterministic fake browser 和 `browser-use` session adapter 两条边界。
- 用工作流拆分规划、浏览、抽取、验证、匹配、保存和报告生成，并记录 sequential / LangGraph 编排模式。
- 在报告和 JSON 中记录 Agent 执行轨迹，展示 planner、browser、extractor、verifier、matcher、reporter 节点的执行摘要。
- 用 SQLite 保存岗位记录和运行指标。
- 根据技能标签和简历文本生成岗位匹配分数、缺失技能和建议动作。
- 汇总所有匹配结果中的技能缺口，帮助判断下一步该补强哪些项目经历。
- 生成本地 HTML Dashboard，展示岗位、匹配分数、优先级、缺失技能、Agent 输入轨迹和 Agent 执行轨迹。
- Dashboard 支持按岗位文本搜索、优先级筛选、匹配分数排序，并展示搜索 query、seed URL、URL 级错误和工作流节点摘要，适合现场演示筛选、调试和 Agent 可观测性。
- 低置信度页面可通过可替换的 LLM 抽取边界恢复结构化字段，支持 deterministic demo、DeepSeek 和 Qwen OpenAI-compatible provider。
- 运行内置 20 任务评测集，统计任务成功率、有效岗位数、平均访问页面数和失败原因分布。
- 用测试中的 fake browser 保证端到端流程可复现。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\web-task-agent.exe --version
.\.venv\Scripts\web-task-agent.exe --doctor
.\.venv\Scripts\web-task-agent.exe --list-fixture-urls
.\.venv\Scripts\web-task-agent.exe --print-demo-script
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --json-output evaluations\llm-comparison.json
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --seed-url "https://example.com/jobs/unstructured-ai-agent-intern" --seed-url "https://example.com/jobs/ai-engineering-intern" --json-output evaluations\seed-comparison.json
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo --dashboard --action-plan --json-output outputs\result.json
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo --langgraph --dashboard
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --target-count 2 --skill Python --resume-file .\resume.md --demo --dashboard
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/ai-engineering-intern" --demo --target-count 1 --json-output outputs\seed-demo.json
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/unstructured-ai-agent-intern" --demo --target-count 1 --llm-extractor-demo --json-output outputs\unstructured-llm-demo.json --dashboard
$env:DEEPSEEK_API_KEY="..."
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/unstructured-ai-agent-intern" --demo --target-count 1 --llm-extractor-provider deepseek --llm-extractor-model deepseek-v4-flash --json-output outputs\deepseek-llm-demo.json
$env:DASHSCOPE_API_KEY="..."
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/unstructured-ai-agent-intern" --demo --target-count 1 --llm-extractor-provider qwen --llm-extractor-model qwen-plus --json-output outputs\qwen-llm-demo.json
.\.venv\Scripts\web-task-agent.exe --history
.\.venv\Scripts\web-task-agent.exe --evaluate --evaluation-count 20
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites --json-output evaluations\fixture-result.json
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites --seed-url "https://boards.greenhouse.io/example/jobs/ai-agent-intern" --json-output evaluations\seed-url-result.json
.\.venv\Scripts\web-task-agent.exe --evaluate --seed-url "https://example.com/jobs/unstructured-ai-agent-intern" --llm-extractor-demo --json-output evaluations\unstructured-llm-result.json
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites --dashboard
.\.venv\Scripts\web-task-agent.exe --evaluate --real-smoke
.\.venv\Scripts\web-task-agent.exe --export-graph
```

如果 Windows PowerShell 显示中文乱码，请使用 UTF-8 终端或执行 chcp 65001 后再查看。

## 已验证的 MVP 命令

```powershell
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo --dashboard --action-plan --json-output outputs\result.json
```

该命令使用内置 demo 页面运行，不依赖真实招聘网站，适合一条命令展示工作流闭环。它会生成 Markdown 报告、本地 HTML Dashboard、Markdown 行动计划和机器可读 JSON；报告会用相对 Markdown 链接列出 Dashboard 和行动计划等相关产物，并包含面试讲述要点，Dashboard 也会展示搜索 query、seed URL、URL 级错误和行动计划等相关产物链接。当前真实 `browser-use` 路径已具备 session adapter 入口，但真实招聘网站表现仍需要单独站点评测和失败原因统计。

加上 `--langgraph` 后，主流程会通过 LangGraph 节点执行，节点包括 planner、browser、extractor、verifier、matcher 和 reporter，适合在面试中展示 Agent 工作流编排。

使用 `--export-graph` 可以生成 `docs/agent-workflow-graph.md`，其中包含 LangGraph 的 Mermaid 工作流图。

使用 `--resume-file .\resume.md` 或 `--resume-text "..."` 可以把简历内容作为岗位匹配信号；两者可同时使用，CLI 会合并后传入 `UserProfile.resume_text`。

使用 `--json-output outputs\result.json` 可以导出完整工作流状态，包含用户输入、岗位、匹配结果、运行指标、报告路径、编排模式和 Agent 执行轨迹；与 `--action-plan` / `--dashboard` 同用时还会在 metadata 中记录行动计划路径、Dashboard 路径和结构化 Top action gaps，方便后续接前端或自动投递流程。

使用 `--action-plan` 可以根据岗位匹配结果生成 Markdown 行动计划，包含优先投递岗位、技能补强顺序、可展示项目任务、简历项目改写要点、7 天执行节奏，以及技术栈体验与面试说法；CLI 也会打印 `Top action gaps`，方便现场直接讲补强重点。

使用 `--seed-url <job-url>` 可以跳过搜索规划，直接打开指定招聘链接；该参数可重复，用于白名单真实站点 smoke 或面试现场稳定演示 exact JD 抽取。

使用 `--llm-extractor-demo` 可以启用 deterministic LLM 风格结构化抽取器，用于演示低结构化 JD 页面如何通过可替换的 LLM 抽取边界恢复为 `JobPosting`，不会调用真实外部 API；该参数可用于普通 workflow 和 `--evaluate` 评测路径。

使用 `--llm-extractor-provider deepseek|qwen` 可以启用真实 OpenAI-compatible LLM 抽取边界；DeepSeek 默认模型为 `deepseek-v4-flash`，读取 `DEEPSEEK_API_KEY`，Qwen 默认模型为 `qwen-plus`，读取 `DASHSCOPE_API_KEY`。也可以用 `--llm-extractor-model` 覆盖模型名。规则抽取仍然先执行，只有低置信度页面才会调用 LLM；CLI 会在 JSON metadata 中记录 `extractor_mode`、`llm_provider` 和 `llm_model`。

使用 `--list-fixture-urls` 可以列出内置 Greenhouse/Lever 风格 fixture URL，便于快速复制到 `--seed-url` 演示或评测命令。

使用 `--doctor` 可以检查当前 Python 路径、虚拟环境状态、关键依赖和输出目录可写性。

使用 `--print-demo-script` 可以输出一组面试现场可复制的演示命令，覆盖环境自检、fixture URL、一键闭环 demo、LangGraph 编排对比、seed URL、LLM extractor demo、DeepSeek provider 示例、运行历史和 fixture evaluation。

使用 `--compare-llm-extractor` 可以对比同一批 seed URL 在规则抽取、deterministic LLM demo 和可选真实 provider 下的评测表现；默认 seed 的当前 baseline 为 `0/1`，LLM demo 为 `1/1`。传入多个 `--seed-url` 后会逐个 URL 生成评测任务，并写出 `evaluations/llm-extractor-comparison.md` 和可选 JSON。

使用 `--history` 可以从 SQLite 读取最近运行记录，快速展示 run_id、有效岗位数、访问页面数和失败页面数。

## 真实 browser-use adapter 状态

非 `--demo` 模式会走 `BrowserUseClient`，通过 `browser_use.BrowserSession` 打开搜索页并读取页面标题和正文。这个路径用于下一阶段真实网页接入；当前推荐演示和评测仍使用 `--demo`，因为它不依赖登录、验证码、反爬策略或外部网页结构变化。

## 已验证的评测命令

```powershell
.\.venv\Scripts\web-task-agent.exe --evaluate --evaluation-count 20
```

该命令会在 `evaluations/` 下生成评测报告。当前内置 demo 评测结果为：20/20 任务完成，任务成功率 1.00，有效岗位总数 40，平均访问页面数 2.00。报告还会输出失败原因分布；真实招聘页风格 fixture 评测可使用 `--evaluate --fixture-sites`，也可以加 `--seed-url` 对单个 exact JD 做稳定评测，并可加 `--dashboard` 生成 `dashboards/evaluation-summary.html`。真实浏览器 smoke 评测可使用 `--evaluate --real-smoke`，用于观察 `browser_error`、`no_pages`、`no_extracted_jobs`、`verification_filtered` 等失败类别。
