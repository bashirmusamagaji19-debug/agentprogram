# 本轮工作：HTTP 失败分类 — browser_error 细分

## 目标

此前所有 HTTP 层面的失败都归为 `browser_error`，无法区分超时、HTTP 状态码错误、空响应等不同场景。这个改动把失败细分为三个可区分类别。

## 为什么需要这个

面试时可以这么说：

> "评测里如果一个 URL 失败，我不能只说 'browser error'。我需要知道是 DNS 解析不到？还是对方返回 404？还是页面是 JS 渲染拿不到正文？每类失败有不同的处理策略——超时要重试，404 要标记 URL 过期，JS 渲染要切 Playwright。"

## 改动内容

### browser.py

- 新增 3 个 `BrowserConfigurationError` 子类：
  - `PageTimeoutError` — DNS 解析失败、连接超时、响应超时
  - `PageHttpError` — HTTP 4xx/5xx，携带 `status_code`
  - `PageEmptyError` — 成功获取 HTML 但正文为空（JS 渲染页面只有 `<script>` 标签）
- `HttpPageLoader._load_sync()` 现在按异常类型分类抛出：
  - `HTTPError` → `PageHttpError`（优先匹配，因为 HTTPError 是 URLError 的子类）
  - `URLError` + socket.timeout → `PageTimeoutError`
  - `URLError` + DNS 失败 → `PageTimeoutError`
  - `TimeoutError` → `PageTimeoutError`
  - 正文为空 → `PageEmptyError`
- `BrowserUseClient.open_url()` 保留特定错误类型（`except BrowserConfigurationError: raise`）

### evaluation.py

- 导入新的错误子类
- 新增 `_classify_browser_error()` 函数映射错误类型 → 失败分类：
  - `PageTimeoutError` → `http_timeout`
  - `PageHttpError` → `http_error`
  - `PageEmptyError` → `empty_page`
  - 其他 → `browser_error`（backward compatible）
- 评测捕获 `BrowserConfigurationError` 时调用该函数

### tests/test_browser.py

- 新增 3 个测试，验证三种错误类型都被正确抛出：
  - `test_http_loader_raises_page_timeout_error_on_connection_failure`
  - `test_http_loader_raises_page_http_error_on_404`
  - `test_http_loader_raises_page_empty_error_on_js_shell`

## 评测验证

用 8 个真实 URL 跑基线评测：

| 失败类别 | 数量 |
|---|---|
| `http_timeout` | 0 |
| `http_error` | 0 |
| `empty_page` | 0 |
| `verification_filtered` | 6 |

当前 8 个 URL 全部可以正常访问并返回正文——没有触发任何 HTTP 层面的失败。分类逻辑通过 mock 测试验证。

## 代码修复

编辑过程中发现并修复了一个 bug：原先 `_load_sync()` 中 `HTTPError` 的 except 块在 `URLError` 之后，但 `HTTPError` 是 `URLError` 的子类，导致 HTTP 错误被通用的 `URLError` 块吞掉。修正为 `HTTPError` 在前，`URLError` 在后。
