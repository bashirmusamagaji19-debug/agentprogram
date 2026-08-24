# Public Demo Deployment Design

**Goal:** 为开放互联网岗位搜索 Agent 提供一个无需本地环境即可访问的公开演示入口，同时保留现有 FastAPI 服务作为工程化接口。

## Architecture

新增 Streamlit 页面作为演示层，直接复用现有 `DemoQueryParser`、`OpenSearchPipeline`、Fixture provider 和 Tavily provider。现有 FastAPI + 原生 HTML 页面继续作为 API/Web 服务入口，两者不复制搜索核心逻辑。

Streamlit 使用 Demo 模式时不需要密钥；Online 模式从云平台 Secrets 读取 `TAVILY_API_KEY` 和 `DASHSCOPE_API_KEY`。云部署以单实例演示为目标，运行记录和 artifact 仍使用当前进程/文件系统边界，并在文档中明确重启丢失限制。

## User flow

1. 用户输入自然语言岗位需求。
2. 用户选择 Demo 或 Online 模式。
3. 页面显示运行状态和错误信息。
4. 页面展示岗位标题、公司、地点、要求、来源链接、证据和可信度。

## Acceptance criteria

- Streamlit 页面能在本地启动并完成 Demo 搜索。
- 缺少在线搜索 key 时显示可理解的错误，不泄露密钥。
- Streamlit 和 FastAPI 共用同一 Pipeline。
- README 包含 Streamlit Cloud、Render 的启动方式和 Secrets 配置说明。
- 增加健康检查接口和生产启动命令说明。
- 现有测试全部通过，并增加 Streamlit 入口的最小 smoke test。
