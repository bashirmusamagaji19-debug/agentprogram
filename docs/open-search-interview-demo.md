# 开放互联网岗位搜索 Agent：面试演示脚本

这份脚本用于现场演示，不依赖外部 API key。完整实现分支为 `feature/open-web-job-agent`。

## 1. 先展示确定性闭环

```powershell
python -m web_task_agent.cli --open-search-demo `
  --query "找北京 Agent 实习，要求 Python 和 LangGraph，1 个岗位" `
  --output-dir outputs/open-search-demo
```

现场说明：系统先解析岗位意图，再验证可信 ATS URL，最后只从详情页 JSON-LD 生成岗位字段和字段证据。搜索摘要只用于发现 URL，不作为最终岗位事实。

重点打开 `run-summary.json` 和 `jobs.jsonl`，指出：

- `terminal_reason=target_reached`
- `final_fields_source=detail_page`
- `extraction_methods.json_ld=1`
- 每个字段证据都有详情页 URL 和 64 位 SHA-256

## 2. 展示失败路径

打开 `docs/results/open-search/detail-page-smoke.md`，说明一个真实 Anthropic Greenhouse 页面成功抽取，另一个失效页面被分类为 `not_job_detail`。再展示 `failures.jsonl`，强调失败不会被伪装成“搜索成功”。

## 3. 展示工程边界

用一句话解释四个边界：

1. 来源边界：只信任单岗位 ATS 页面或显式配置的公司招聘主机。
2. 网络边界：逐跳检查重定向，并检查 DNS 解析到的地址，拒绝私网和保留地址。
3. 资源边界：详情页正文有超时、大小和重定向次数上限。
4. 副作用边界：artifact 损坏、run 淘汰和缺少 key 都有结构化错误，不直接冒泡为不可解释的 500。

## 4. 有 key 时再展示在线模式

```powershell
python -m web_task_agent.open_search.online_smoke `
  --query "Find remote AI application engineering internships requiring Python, top 1 jobs" `
  --output-dir outputs/open-search-online-smoke/manual
```

只有配置有效 `TAVILY_API_KEY` 后才运行这一步。报告中的岗位数、失败分类和终止原因与离线 fixture 指标分开解释；没有 key 时不要生成伪在线报告。

## 面试收束句

“这个项目的重点不是把搜索结果拼成一张表，而是把发现、详情验证、字段证据和失败分类拆成可测试的 Agent 边界。LLM 可以参与语义选择，但来源可信度、预算、重定向、资源和副作用由确定性策略兜底。”
