# Web 自动任务 Agent

这是一个面向 AI 工程 / AI 应用实习的 Agent 项目。当前版本在 `browser-use` 与 `LangGraph` 工作流之上增加了 Hybrid Decision Agent：可选 DeepSeek/Qwen 结构化规划器负责语义选择，确定性策略负责安全、预算、重试和降级；Agent 根据观察结果动态选择工具，并输出可审计的决策与恢复轨迹。

## 简历项目入口

这是一个用 Python、LangGraph、browser-use、Pydantic 和 SQLite 构建的 Hybrid Web Task Agent：LLM 负责受约束的语义选工具，确定性 policy 负责动作白名单、预算、恢复、终止和外部副作用安全。

最能体现 Agent 应用开发的能力是两条边界：`execution_trace` 让每次决策可复盘；Human-in-the-loop 在 `save_results` 前用 LangGraph `interrupt` 暂停，凭稳定 `thread_id` 跨进程恢复，并用 `approval_id` receipt 防止 replay 重复写入。拒绝路径以 `human_denied` 结束且不保存。

当前可复核证据：本轮全量测试 `360 passed`；Hybrid deterministic fixture `10/10` 循环终止、HITL `3/3` 暂停、拒绝副作用 `0`、重复副作用 `0`。这些数字来自测试输出和版本化 artifact，不代表真实招聘网站泛化准确率。

```powershell
# 无 API key、无 GPU、无云服务器：生成一套可面试展示的离线证据
.\.venv\Scripts\web-task-agent.exe --portfolio-demo `
  --portfolio-demo-output-dir portfolio-artifacts

# 发布前运行与 GitHub Actions 一致的本地质量门禁
.\.venv\Scripts\web-task-agent.exe --release-check
```

Portfolio 产物包括 Hybrid 决策 JSON/Markdown/HTML、HITL approve/reject/replay JSON/Markdown 和阶段汇总。项目故事、简历三条、60 秒讲法见 `docs/interview-benchmark-story.md`；完整实现日志见 `docs/work-log/2026-07-30-resume-portfolio-finish.md`。

## 开放互联网岗位搜索 Agent

输入自然语言岗位需求，系统先解析地点、技能、数量和排除条件，再通过 Fixture（离线）或 Tavily（在线）发现候选 URL；Online 模式还会请求详情页，检查最终重定向域名必须保持原可信主机或同一 ATS 域族，拒绝不可达、空正文、非 HTML 或不可信重定向页面，并在执行轨迹中记录页面正文 SHA-256（不保存整页内容），最后只把可信招聘详情页和页面字段证据作为结果。搜索摘要不是最终证据；开放搜索不保证每次都有结果，`search_api_error`、`source_untrusted`、`page_unreachable`、`page_not_html`、`page_empty`、`redirect_untrusted`、`no_match` 和 `budget_exhausted` 会分别记录。

```powershell
# 离线演示：无需 key，生成岗位、执行轨迹和 run-summary
python -m web_task_agent.cli --open-search-demo --query "找北京 Agent 实习" --output-dir outputs/open-search

# Web 运行台
python -m uvicorn web_task_agent.open_search.api:app --reload
# 浏览器打开 http://127.0.0.1:8000/

# 公开岗位搜索 Streamlit Demo
python -m pip install -e ".[demo]"
python -m streamlit run streamlit_app.py
# 浏览器打开 Streamlit 输出的本地地址，默认 http://localhost:8501

# 冻结查询评测（与在线审计指标分开）
python -m web_task_agent.open_search.evaluation --queries data/open-search/evaluation/queries.jsonl --output-dir docs/results/open-search
```

当前冻结的 20 条查询评测结果为：需求解析正确率 `100.0%`、硬约束违反 `0`。该结果只证明固定查询集上的解析回归，不代表开放互联网岗位搜索的召回或抽取准确率。

简历表述：实现基于搜索 API + 浏览器验证的开放互联网岗位搜索 Agent；建立来源可信度、字段证据和失败分类边界；用 20 条自然语言查询和版本化 artifact 验证可复现性。冻结 fixture 指标不等同于真实互联网泛化准确率。

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
- Hybrid Agent 提供 8 个类型化工具：搜索、打开页面、文本抽取、视觉抽取、验证、匹配、保存和结束。
- LangGraph 使用 `decide -> execute_tool -> observe -> guard` 条件循环；步数预算、URL 重试上限和终止条件由代码策略强制执行。
- 搜索结果会解析为真实候选 JD 链接，不再把 Google 搜索页误当作岗位页。
- 规划器输出无效、页面打开失败、低置信度抽取或 verifier 拒绝时，自动走确定性 fallback、换 URL 或 text-to-visual 恢复。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src\web_task_agent\agent_*.py src\web_task_agent\search_discovery.py tests\test_agent_*.py tests\test_search_discovery.py
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
# LLM 语义匹配（demo / DeepSeek provider）
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --target-count 2 --skill Python --skill FastAPI --resume-text "Built REST APIs with FastAPI." --demo --llm-match --json-output outputs\semantic-match.json
$env:DEEPSEEK_API_KEY="..."
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --target-count 2 --skill Python --skill FastAPI --resume-text "Built REST APIs with FastAPI." --demo --llm-match-provider deepseek --json-output outputs\deepseek-match.json
# 真实站点 LLM 全链路对比
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --real-site-sample --evaluation-count 8 --llm-extractor-provider deepseek --json-output evaluations\final-comparison.json
# LLM 语义匹配对比评测
.\.venv\Scripts\web-task-agent.exe --compare-llm-match --real-site-sample --evaluation-count 8 --llm-extractor-provider deepseek --llm-match-provider deepseek --skill Python --skill LangGraph --skill FastAPI --resume-text "Built REST APIs with FastAPI. Built LangGraph agents." --json-output evaluations\match-comparison.json
.\.venv\Scripts\web-task-agent.exe --evaluate --evaluation-count 20
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites --json-output evaluations\fixture-result.json
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites --seed-url "https://boards.greenhouse.io/example/jobs/ai-agent-intern" --json-output evaluations\seed-url-result.json
.\.venv\Scripts\web-task-agent.exe --evaluate --seed-url "https://example.com/jobs/unstructured-ai-agent-intern" --llm-extractor-demo --json-output evaluations\unstructured-llm-result.json
.\.venv\Scripts\web-task-agent.exe --evaluate --fixture-sites --dashboard
.\.venv\Scripts\web-task-agent.exe --evaluate --real-smoke
.\.venv\Scripts\web-task-agent.exe --export-graph
# 稳定的 Hybrid Agent 演示（无 API key）
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --demo --hybrid-agent --target-count 1 --agent-max-steps 8 --db-path ":memory:" --json-output outputs\hybrid-agent-demo.json
# 生成版本化的 10 场景评测证据
.\.venv\Scripts\python.exe -m web_task_agent.agent_evaluation --output-dir docs\results
```

## 公开 Web 演示部署

项目提供两个入口：`streamlit_app.py` 是面试官直接体验的演示页面，FastAPI 是工程化 API 入口。

### Streamlit Community Cloud（推荐演示）

1. 将仓库推送到 GitHub。
2. 在 Streamlit Community Cloud 选择仓库，Main file 设置为 `streamlit_app.py`。
3. 在 App Settings / Secrets 中配置：

```toml
TAVILY_API_KEY = "你的 Tavily key"
# 仅在启用项目中其他 Qwen 抽取/规划 CLI 时需要
DASHSCOPE_API_KEY = "你的 DashScope key"
```

Demo 模式无需任何 key；当前 Web Online 模式使用 Tavily，Qwen key 对该页面不是必需项。部署完成后平台会生成 `https://<app>.streamlit.app` 公网地址。

### Render（FastAPI 服务）

仓库中的 `render.yaml` 已定义构建和启动命令。Render 使用以下生产启动方式：

```text
uvicorn web_task_agent.open_search.api:app --host 0.0.0.0 --port $PORT
```

部署后可通过 `/healthz` 检查服务状态，并获得 `https://<service>.onrender.com` 地址。API key 应在 Render Environment 中配置，不能提交到仓库。

API 客户端还可以访问 `/api/capabilities`，查看 Demo/Online 模式是否可用；该接口只返回布尔状态，不会返回任何密钥内容。

Online verifier 还会拒绝 `localhost`、环回、私有、链路本地和保留 IP，防止开放搜索结果诱导服务端访问内网资源；这是演示服务的 SSRF 基础防护，不替代生产环境的网络出口策略。

### Docker（通用部署）

```powershell
docker build -t open-web-job-agent .
docker run --rm -p 8000:8000 -e TAVILY_API_KEY="你的 key" open-web-job-agent
```

容器默认启动 FastAPI，使用 `PORT` 环境变量覆盖监听端口；启动后访问 `/healthz` 检查服务状态。
本地构建前需要启动 Docker Desktop 的 Linux engine；CI 或云平台会在自己的 Docker daemon 中执行构建。

当前版本是单实例演示部署：运行状态保存在进程内存（最多保留最近 100 次 run），artifact 写入实例本地文件系统，实例重启后历史运行记录可能丢失；这不影响现场演示，但不应当作多实例生产存储方案。
单实例 API 默认按客户端 IP 限制每分钟创建 20 次 run，最多保留 100 次 run；可通过 `OPEN_SEARCH_RATE_LIMIT_PER_MINUTE` 和 `OPEN_SEARCH_MAX_RUNS` 调整，超过限流时返回 `429 rate_limited`。多实例生产部署应将限流状态迁移到共享网关或 Redis。

本地先验证 Streamlit 页面：

```powershell
python -m pip install -e ".[demo]"
python -m streamlit run streamlit_app.py
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

使用 `--real-site-sample` 可以切换到一组固定的真实招聘页样本，便于做更接近真实页面的 `--evaluate` 和 `--compare-llm-extractor` 对比；该模式保留真实 URL，但内容采集走 HTTP loader，默认保留规则抽取、deterministic LLM demo 和可选 DeepSeek/Qwen provider 的同批对比。历史 2026-06-22 的 8 页固定样本结果为 DeepSeek 7/8 任务完成（88%）；这是完成率而非字段准确率，真实页面漂移后必须重新运行。

## Hybrid Decision Agent

Hybrid 模式不是让 LLM 直接控制浏览器。LLM 只能从白名单动作中提出结构化决策；`DeterministicAgentPolicy` 对目标数量、剩余步数、每 URL 重试次数和终止原因拥有最终控制权。未配置 API key 时仍可完整运行确定性策略；配置 `--agent-planner-provider deepseek|qwen` 后才启用语义规划。

两条可演示的恢复链：

1. `open_page` 失败后在重试预算内重试，耗尽后选择下一个候选 URL。
2. 文本抽取低置信度或 verifier 拒绝后，若视觉工具可用则转向 `extract_visual`；否则跳过当前页面。

公开证据位于 `docs/results/hybrid-agent-benchmark.md`。`hybrid-agent-deterministic-v1` 包含 10 个合成确定性场景：业务目标完成率 80%、循环终止率 100%、工具成功率 88.46%。字段准确率只针对带显式 ground truth 的 fixture 计算，不代表真实网站泛化能力。

### 真实 Planner 对照评测

下面的命令让 deterministic policy、DeepSeek 和 Qwen 在同一批 5 个受控 runtime 场景中决策：

```powershell
.\.venv\Scripts\web-task-agent.exe --agent-planner-benchmark --agent-planner-benchmark-providers deterministic,deepseek,qwen --agent-planner-benchmark-output-dir docs/results/planner-benchmark
```

2026-07-29 的真实 API 运行结果：

| Planner | 模型 | 任务完成 | 循环终止 | Planner 调用 | 非法决策 / fallback | 平均步数 | Planner 延迟 | Total Token |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| deterministic | deterministic-policy-v1 | 4/5 | 5/5 | 0 | 0 / 0 | 3.8 | 0 ms | 0 |
| DeepSeek | deepseek-v4-flash | 4/5 | 5/5 | 15 | 5 / 5 | 3.4 | 50.37 s | 5518 |
| Qwen | qwen-plus | 4/5 | 5/5 | 16 | 0 / 0 | 3.2 | 28.10 s | 5077 |

Qwen 在 `open-recovery` 中直接选择有效候选 URL，将该场景从 deterministic 的 5 步降至 3 步；DeepSeek 的 5 次未授权决策全部被 runtime 拒绝并转入确定性 fallback，仍保持 5/5 正常终止。这证明 LLM 能优化候选选择，但没有权限绕过 URL 白名单、失败恢复、预算和终止规则。

公开证据位于 [`docs/results/planner-benchmark/planner-benchmark.md`](docs/results/planner-benchmark/planner-benchmark.md) 和对应 JSON。该结果使用真实 DeepSeek/Qwen API，但网页与故障均为受控 fixture，因此衡量 Planner 决策与安全 fallback，不代表真实招聘网站抽取泛化能力；延迟与 Token 也是本次单次运行快照。

项目叙述和 benchmark 归纳见 `docs/interview-benchmark-story.md`。

使用 `--history` 可以从 SQLite 读取最近运行记录，快速展示 run_id、有效岗位数、访问页面数和失败页面数。

## Human-in-the-loop 暂停与恢复

Hybrid Agent 可以在 `save_results` 前通过 LangGraph `interrupt` 暂停。状态由
`langgraph-checkpoint-sqlite` 持久化，程序退出后仍可使用同一个 `thread_id` 恢复：

```powershell
# 首次运行：执行到 save_results 前暂停
.\.venv\Scripts\web-task-agent.exe --demo --hybrid-agent --hitl `
  --thread-id interview-demo-001 `
  --checkpoint-db .agent\checkpoints.sqlite `
  --db-path agent.db --keyword "AI Agent intern" --target-count 1

# 使用首次运行打印的 approval-id 批准
.\.venv\Scripts\web-task-agent.exe --hybrid-agent --hitl `
  --thread-id interview-demo-001 `
  --checkpoint-db .agent\checkpoints.sqlite `
  --db-path agent.db --approval-id <approval-id> --resume-approval approve

# 也可以把最后一个参数改成 reject；该路径以 human_denied 结束且不保存结果
```

审批 payload 只包含审批 ID、动作、岗位数量、摘要和时间，不包含简历正文、页面正文或
API key。批准后使用 `approval_id` 作为业务数据库幂等键；即使 checkpoint 在保存后重放，
也不会产生第二次可见副作用。JSON、Markdown 和 HTML 输出包含 requested/resolved 审计轨迹。

本地确定性证据位于 `docs/results/hitl-checkpoint/`：3/3 场景成功暂停，拒绝路径副作用为
0，重复副作用为 0。该评测使用受控 fixture，不衡量真实招聘网站抽取质量。本功能不需要 GPU、
云服务器或模型训练。

## 真实 browser-use adapter 状态

非 `--demo` 模式会走 `BrowserUseClient`，通过 `browser_use.BrowserSession` 打开搜索页并读取页面标题和正文。这个路径用于下一阶段真实网页接入；当前推荐演示和评测仍使用 `--demo`，因为它不依赖登录、验证码、反爬策略或外部网页结构变化。

## Visual extractor demo

视觉抽取路径是一个实验性的 seed URL 模式，用截图/VLM 风格的确定性 fixture 替代文本抽取，用于验证 visual-web-agent 思路在 Agent 工作流中的表现，不改变默认文本抽取路径。

```powershell
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/visual-ai-intern" --demo --target-count 1 --visual-extractor-demo --json-output outputs\visual-demo.json
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --seed-url "https://example.com/jobs/visual-ai-intern" --visual-extractor-demo --json-output evaluations\visual-comparison.json
```

当前范围：

- 使用确定性 visual fixture 做可复现的本地验证。
- 产出标准 `JobPosting`，verifier、matcher、reports、dashboards 和 JSON 输出不受影响。
- 视觉抽取失败时回退到文本抽取，不破坏现有闭环。

### Real visual provider（需安装 sibling package）

真实 Qwen-VL 视觉抽取链路位于同级 `visual-web-agent` 仓库。先安装到同一个 virtualenv：

```powershell
python -m pip install -e "..\visual-web-agent"
```

**Provider smoke 命令**（需要真实、可公开访问的招聘 URL）：

```powershell
.\.venv\Scripts\web-task-agent.exe --seed-url "https://job-boards.greenhouse.io/anthropic/jobs/5116927008" --target-count 1 --visual-extractor-provider qwen-vl --json-output outputs\visual-provider.json
```

Provider smoke 如果 `Valid jobs: 0`，会在写入诊断信息和 JSON 后返回退出码 `2`，防止空抽取看起来像验证通过。

**对比评测命令**（comparison 始终返回退出码 `0`，即使某条 provider 行失败——对比评测是做横向测量）：

```powershell
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --seed-url "https://example.com/jobs/visual-ai-intern" --visual-extractor-provider qwen-vl --json-output evaluations\visual-provider-comparison.json
```

真实 provider 自带 Playwright 浏览器，workflow 不会对 seed URL 重复获取——`_browser_node` 检测到 `uses_own_browser` 后跳过 workflow browser，直接创建占位 `BrowserPage`，由 provider 自己截图抽取。VLM 调用成功但返回空字段（`Unknown Title`、空 body、零置信度）时，adapter 质量门将其计为抽取失败，不污染 `visual_extraction.successes` 计数。

## Real Site Benchmark V2

`--benchmark-v2` 在真实站点评测目录上运行 provider matrix，是面试展示"不只是 demo"的核心 artifact。

```powershell
.\.venv\Scripts\web-task-agent.exe --benchmark-v2 --benchmark-providers baseline,llm-demo,deepseek --benchmark-limit 8 --benchmark-dashboard --benchmark-explain
```

产出物：

- `evaluations/benchmark-v2.json`：机器可读的 case catalog 和 provider matrix。
- `evaluations/benchmark-v2.md`：Markdown 报告（provider 成功率 + 失败分类）。
- `evaluations/benchmark-v2-explained.md`：中文解释层（一句话结论、provider 解读、失败分析、面试讲法）。面试时用这个。
- `dashboards/benchmark-v2.html`：本地 HTML 摘要（面试演示用）。
- `evaluations/<provider>/evaluation-report.md`：每个 provider 的逐任务详情。

当相关 API key 和 `visual-web-agent` sibling package 已配置时，可使用完整 provider 集：`--benchmark-providers baseline,llm-demo,deepseek,qwen,qwen-vl`。真实 URL 可能漂移，失败以 `failure_counts` 记录而非隐藏。

## 已验证的评测命令

```powershell
.\.venv\Scripts\web-task-agent.exe --evaluate --evaluation-count 20
```

该命令会在 `evaluations/` 下生成评测报告。当前内置 demo 评测结果为：20/20 任务完成，任务成功率 1.00，有效岗位总数 40，平均访问页面数 2.00。报告还会输出失败原因分布；真实招聘页风格 fixture 评测可使用 `--evaluate --fixture-sites`，也可以加 `--seed-url` 对单个 exact JD 做稳定评测，并可加 `--dashboard` 生成 `dashboards/evaluation-summary.html`。真实浏览器 smoke 评测可使用 `--evaluate --real-smoke`，用于观察 `browser_error`、`no_pages`、`no_extracted_jobs`、`verification_filtered` 等失败类别。
