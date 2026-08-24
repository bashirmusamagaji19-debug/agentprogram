# 开放搜索岗位 Agent 工作日志

## 2026-08-24

- 目标：把岗位搜索从固定来源扩展到“搜索 API 发现 + 浏览器/详情页证据验证”。
- 已完成：领域模型、中文解析、Tavily/Fixture provider、可信来源判定、字段哈希证据、审计 pipeline、FastAPI 运行台、原生前端和 20 条冻结查询评测。
- 关键边界：搜索摘要不作为证据；`http/https` 之外的 URL 拒绝；provider 失败保留 `search_api_error`；候选阶段可无 hash，最终字段证据必须有 hash。
- 真实故障记录：百度页面的 JS-like `undefined` 不能直接 JSON 解析；SQLite `:memory:` 多连接导致 receipt 表不可见；这些问题保留在既有面试材料中。
- 未完成：完整 40 个真实冻结岗位详情页、3 轮在线审计和 Hybrid policy 对开放搜索工具的深度接入，不能在当前报告中虚构为已完成。
