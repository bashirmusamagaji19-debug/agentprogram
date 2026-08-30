# CLI 离线演示运行时 Smoke

## 命令

```powershell
python -m web_task_agent.cli --open-search-demo `
  --query "找北京 Agent 实习，要求 Python 和 LangGraph，1 个岗位" `
  --output-dir outputs/open-search-final-smoke-fixed-20260830
```

## 结果

- 终止原因：`target_reached`
- 候选数：`1`
- 可信岗位数：`1`
- 失败数：`0`
- 最终字段来源：`detail_page`
- 抽取方法：`json_ld`
- 核心证据完整岗位数：`1`
- 岗位字段：`Agent Intern / Example AI / Beijing, CN`
- 证据字段：`title, company, location, employment_type, requirements, description`
- 所有字段证据绑定同一 64 位 SHA-256：`565e270e15d22014eb7af5d939c01e71dc1671f90d329600581acb7f2732a579`

## 边界

这是无 API key 的确定性 fixture 运行时证据，证明 README 中的离线演示入口可产生完整岗位 artifact；它不代表 Tavily 开放互联网搜索的召回或真实性能。

后续来源验证新增 HTTPS 到 HTTP 降级重定向拒绝测试；全量测试当前为 `404 passed`，覆盖率 `90.71%`。
