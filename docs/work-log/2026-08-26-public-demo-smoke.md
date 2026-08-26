# Public Demo 当前版本 Smoke 记录

## 本地进程验证

使用当前 `feature/open-web-job-agent` 代码启动 Uvicorn，并通过真实 HTTP 请求检查：

- `/healthz` -> `200 {"status":"ok"}`
- `/readyz` -> `200 {"status":"ready","artifact_writable":true}`
- `/api/version` -> `200`，返回项目版本和非敏感运行限制
- `/api/capabilities` -> `200`，当前 Demo 可用、Online 因未配置 Tavily key 而不可用
- `POST /api/runs` 使用 `" DEMO "` -> `202`，模式规范化成功，`target_count=2`
- 完成后的 `/api/runs/{run_id}/evaluation` -> `200`，`available=true`

## 边界

本次没有配置或输出任何 API key；Online 真实搜索仍需在云平台 Secrets 中配置 `TAVILY_API_KEY` 后单独 smoke 验证。
