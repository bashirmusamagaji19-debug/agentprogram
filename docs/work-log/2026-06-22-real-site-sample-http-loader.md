# 2026-06-22 Real Site Sample HTTP Loader

## 本轮目标

让 `--evaluate --real-site-sample` 和 `--compare-llm-extractor --real-site-sample` 在本机可稳定产出真实样本结果，不再依赖当前环境里失效的 `browser-use` 正文读取。

## 过程

1. 先实跑真实站点样本，确认 `BrowserUseClient` 在当前环境里对真实 URL 返回空 title/content。
2. 用 `session_factory` 和 `page_loader` 的测试把问题定位到正文采集层，而不是 verifier 或 extractor。
3. 新增 `HttpPageLoader`，用标准库 `urllib.request` 直接抓取真实 URL 并解析 title/body 文本。
4. 将真实站点样本模式改为注入 `HttpPageLoader`，保留 `BrowserClient` 接口边界。

## 结果

- `--evaluate --real-site-sample --evaluation-count 2` 成功，输出 `Completed tasks: 2/2`。
- `--compare-llm-extractor --real-site-sample --evaluation-count 2` 成功，输出 `baseline: 2/2` 和 `llm-demo: 2/2`。
- 新增浏览器测试覆盖 `HttpPageLoader` 的 title/body 抽取。

## 说明

- 这次修复保留了真实 URL，但把内容采集从失效的 browser-use 正文读取切到了 HTTP 抓取。
- 真实样本模式现在更适合做稳定对比，而不是依赖浏览器环境状态。

## 验证

- `pytest tests/test_browser.py -q`
- `pytest -q`
- `.\.venv\Scripts\web-task-agent.exe --evaluate --real-site-sample --evaluation-count 2 --json-output evaluations\real-site-smoke.json`
- `.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --real-site-sample --evaluation-count 2 --json-output evaluations\real-site-compare.json`

