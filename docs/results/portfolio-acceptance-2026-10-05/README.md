# 实习项目收尾验收（2026-10-05）

本轮目标是把已有能力变成可演示、可复核的交付，停止扩展功能。原始运行放在 `portfolio-artifacts/acceptance-2026-10-05/`，本目录保留结果、轨迹与复现入口。

## 结果与证据

| 能力 | 本轮观察 | 证据 |
|---|---|---|
| 真实搜索 | 大模型 + 北京、上海、北京/上海三轮各返回 2 条；返回岗位无关键词/工作地违规 | [live-search.json](live-search.json) |
| 抽取 | 3 个受控页面：规则完成 2/3，Qwen 增强路径 3/3，确定性视觉 adapter 3/3 | [extraction-comparison.json](extraction-comparison.json) |
| 匹配 | 16 条合成标注样本：规则 15/16、纯 Qwen 13/16、混合 15/16；无模型调用失败 | [逐条对比](matching/matcher-evaluation.md)、[摘要](matching/summary.json) |
| 冻结 JD 回放 | 规则及 deterministic LLM demo 各 3 轮，37 条正文均抽取成功，3 个已知来源失败保留 | [snapshot-summary.json](snapshot-summary.json) |
| 实际 Agent 恢复 | 超时 URL 重试耗尽后换 URL；低置信度文本转视觉，两条实际 runtime 都以 target_reached 结束 | [agent-recovery.json](agent-recovery.json) |
| 真实 Qwen 规划 | Demo 页面上 5 次规划调用，1 次无效/未授权决策被 fallback 接管；成功完成、匹配并保存 1 条 | [qwen-planner.json](qwen-planner.json) |
| 离线演示 | 实际 Hybrid runtime 完成目标；HITL benchmark 3/3 暂停、拒绝/重复副作用均为 0 | [Hybrid](portfolio-hybrid.json)、[HITL](portfolio-hitl.json)、[Dashboard](hybrid-demo.html) |
| 独立进程 HITL | start/resume 使用不同 CLI 进程；批准保存 1 条，拒绝保存 0 条，重复 resume 被拒绝且不新增写入 | [cross-process-hitl.json](cross-process-hitl.json) |
| 本地重启恢复 | 创建和恢复进程 PID 不同；4 项产物 sha256 一致，其他 owner 不可读 | [local-persistence.json](local-persistence.json) |
| 发布检查 | 六阶段本地 release-check，测试/覆盖率、构建、doctor、strict HITL 与 whitespace 检查 | [release-check.txt](release-check.txt) |

### 搜索验收口径

来源限定 `tencent-campus` 与 `xiaomi`，走 Streamlit 同一 `run_ui_request`，每轮目标 2。三轮耗时约 14.00、8.65、9.96 秒；分别有 8、2、8 个候选页面访问失败，包含真实 404，不隐藏失败以展示完整成功。所有返回岗位均为当次官方 API 验证正文。

同一个岗位可以标注北京、上海等多个工作地，因此不同城市搜索可能返回相同岗位；条件要求实际工作地包含指定城市之一。候选采样有上限，不能用这 6 条结果推导覆盖率或长期可用率。来源状态为 healthy 也不意味着每条详情链接可用。

### 抽取与匹配口径

抽取样本是已知的结构化、低结构化及视觉 Demo 页面，Qwen 增强路径只在规则低置信度时调用真实模型。2/3 与 3/3 是任务完成数，未在本次三页对比中建立独立逐字段 ground truth，因此不作为字段准确率。

匹配使用既有 `evaluations/ground-truth/matching.jsonl` 的 16 条合成标注案例，阈值 0.4；纯模型和混合方案共用同一次模型响应。Qwen 补对 #5 语义边界案例，同时在 #8、#12、#14 上抬高分数；混合方案仍在 #14 上误判。结果支持解释规则优先与分数折减的设计取舍，不能声称模型整体优于规则，也不在这 16 条上继续调参数以追求满分。

冻结 JD 使用固定合成候选人资料；六轮回放结果相同，deterministic LLM demo 没有增加收益。规则 Title 为 37/37、Company 为 37/40（含 3 个来源失败）、相关性分类为 30/37；P@6/R@6 为 4/6，NDCG@6 为 0.7409。地点仅有 2 个标注，技能没有独立标注，不宣传地点/技能泛化准确率。这些正文来自 2026-09-09 历史归档，不是本轮实时网页采集。

### 能力入口与未验收项

- 网页运行台：固定 LangGraph 工作流，完成发现、联合过滤、抽取、匹配、报告与历史恢复。
- Hybrid/HITL：独立 CLI 入口，可展示决策循环、恢复、预算和审批；网页尚未接入它们。
- 视觉恢复：本轮为确定性 adapter 对实际 runtime 的路由验收，未调用真实外部 VLM，不能宣传真实视觉字段准确率。
- 云端 PostgreSQL/S3：代码 adapter 保留，未完成真实资源部署验收；唯一跳过的集成测试未配置 TEST_DATABASE_URL。
- Git：当前分支 `fix/source-reliability-finish`，保留原有混合未提交改动；未自动提交、推送或合并。Python 3.11 的远端 CI 本轮未触发，本地使用 Python 3.12.7。

## 三分钟演示

1. **前 45 秒：网页主链路。** 启动运行台，选择内置 Demo，关键词 `AI intern`、地点 `上海`、目标 1，展示岗位地点 Shanghai；换北京展示无匹配说明。真实来源展示本轮保存的结果与 404 诊断，避免把实时站点波动当作稳定离线演示。
2. **45–90 秒：模型取舍。** 展示抽取的 2/3 → 3/3，以及匹配 #5、#14。解释模型能恢复低结构化内容和语义边界，同时可能乐观打分。
3. **90–135 秒：Agent 恢复。** 打开 agent-recovery.json，沿 open_page 失败、重试、换 URL 的链条，以及 extract_text → extract_visual → verify_job 的链条讲解。真实 Qwen 的无效决策由 fallback 接管有单独轨迹。
4. **135–180 秒：审批与交付。** 展示审批暂停、批准/拒绝/重复恢复的保存数量，再展示四项产物哈希与 release-check 六阶段结果。明确网页与独立 CLI 的边界。

离线演示不需要 API key：

```powershell
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
.\.venv\Scripts\web-task-agent.exe --portfolio-demo --portfolio-demo-output-dir portfolio-artifacts
.\.venv\Scripts\python.exe scripts\accept_agent_recovery.py
.\.venv\Scripts\python.exe scripts\accept_local_recovery.py
.\.venv\Scripts\web-task-agent.exe --release-check
```

搜索复跑需要公网，不调用 LLM：

```powershell
.\.venv\Scripts\python.exe scripts\accept_live_search.py
```

真实 Qwen 对比需要服务进程的 DASHSCOPE_API_KEY；本轮只发送受控页面与合成标注资料：

```powershell
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --demo `
  --seed-url https://example.com/jobs/unstructured-ai-agent-intern `
  --seed-url https://example.com/jobs/ai-engineering-intern `
  --seed-url https://example.com/jobs/visual-ai-intern `
  --llm-extractor-provider qwen --visual-extractor-demo `
  --evaluation-dir portfolio-artifacts\acceptance-rerun\extraction `
  --json-output portfolio-artifacts\acceptance-rerun\extraction.json
.\.venv\Scripts\web-task-agent.exe --evaluate-matcher --llm-match-provider qwen `
  --evaluation-dir portfolio-artifacts\acceptance-rerun\matching `
  --json-output portfolio-artifacts\acceptance-rerun\matching.json
.\.venv\Scripts\python.exe scripts\run_real_job_benchmark.py --offline `
  --providers snapshot-baseline,snapshot-llm-demo --runs 3 --recommendation-k 6 `
  --output-dir portfolio-artifacts\acceptance-rerun\snapshot
```

模型复跑输出到新的 ignored 目录，原始版本化评测不覆盖。三个 acceptance 脚本会刷新本目录的本轮验收 JSON，复跑前应保留需要归档的证据。
