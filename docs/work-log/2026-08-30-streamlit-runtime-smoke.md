# Streamlit 运行时 Smoke

## 验证范围

在当前 `feature/open-web-job-agent` 分支启动 `streamlit_app.py`，使用 headless 模式监听本机 `127.0.0.1:8511`，再通过 HTTP 客户端访问根路径。

## 结果

- 进程成功启动并输出 Streamlit 访问地址。
- `GET /` 返回 HTTP `200`。
- 响应长度为 `1522` 字节，包含 Streamlit 页面标识。
- smoke 结束后主动停止临时进程，没有遗留服务。

## 边界

该结果证明 Streamlit 入口和依赖在本地运行环境可启动，不代表 Streamlit Cloud 已完成账号授权、Secrets 配置或公网访问；在线搜索仍需有效 `TAVILY_API_KEY`。

## 后续 API 稳定性回归

artifact JSONL 损坏时，API 现在返回 `503 artifact_corrupt`，不再将 `JSONDecodeError` 冒泡为未分类的 `500`。新增测试覆盖错误码和响应结构；全量测试当前为 `402 passed`，覆盖率 `90.75%`。

## 详情页资源边界

在线验证新增 `OPEN_SEARCH_MAX_PAGE_BYTES`，默认只允许最多 2,000,000 字节的 HTML 进入哈希和抽取链路，超限记录为 `page_too_large`。该应用层处理上限不替代代理或网关的实际下载限额。
