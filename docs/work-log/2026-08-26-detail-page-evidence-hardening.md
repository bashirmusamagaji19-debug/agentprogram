# 详情页字段证据与来源验证加固

## 审计发现

原 Open Search Pipeline 会在线请求详情页并记录正文哈希，但最终 `VerifiedJob` 的标题、公司和证据仍来自搜索 provider 的标题/摘要。这与“搜索摘要只做发现，最终字段来自详情页”的设计边界冲突。

## 本轮修改

- 新增 `detail_extractor.py`，优先读取 `JobPosting` JSON-LD。
- 增加受限的 Greenhouse OpenGraph fallback，用 `og:title`、`og:description` 和页面 title 提取标题、地点、公司。
- Pipeline 只接受详情页正文生成的字段；缺少正文或核心字段时记录 `extraction_incomplete`。
- 字段证据统一绑定最终页面 URL 和整页 SHA-256。
- 持久化岗位描述限制为 8,000 字符、职责/要求各 4,000 字符、证据片段 500 字符；不保存整页正文。
- run summary 增加 `final_fields_source=detail_page`、抽取方法计数和核心证据完整岗位数。
- 重定向改为逐跳预检查，不再先请求不可信目标再事后拒绝。
- 同一 ATS 子域跳转允许；ATS 招聘板或错误页归类为 `not_job_detail`。
- 默认来源收紧为 Greenhouse、Lever、Workday、Ashby 单岗位页；公司自建招聘主机需显式配置。
- Streamlit 与 FastAPI Web 页面展示字段值、截断证据片段和正文哈希。

## TDD 与验证

- 详情抽取器测试先因模块不存在失败，随后 4 条通过。
- Pipeline 的搜索摘要污染、在线正文与缺失正文测试先失败，随后 5 条通过。
- 重定向预检查、ATS 域族、错误页和来源白名单测试均完成红绿循环。
- 开放搜索测试：定向回归通过，整组测试通过（当前共 87 条）。
- 全量测试：`395 passed`，`7 warnings`。
- 总覆盖率：`90.56%`。
- Ruff 与 Python 编译检查通过。
- 后续回归修复：所有 Pipeline summary 收尾路径写入 UTC `finished_at`；在线模式缺少 `TAVILY_API_KEY` 时在限流和 run 创建前直接返回结构化错误且无副作用；Greenhouse OpenGraph 公司名前缀解析改为大小写不敏感。

## 真实页面证据

`docs/results/open-search/detail-page-smoke.*` 记录 2026-08-26 的两条公开页面 smoke：Anthropic 页面成功抽取 4 条证据；Reddit 历史 URL 被 `not_job_detail` 拒绝。页面正文未提交仓库。

## 未完成边界

本轮没有 Tavily key，因此仍未完成 3 轮“搜索 API 发现 + 详情验证”的独立在线验收；公开 Streamlit 部署仍停在用户账号登录授权之前。

## 后续真实运行证据

- 2026-08-27 本地 Uvicorn HTTP smoke：`POST /api/runs` 返回 run `0c40e612475244abb6ae99c47fde9742`，`/evaluation` 中 summary 使用同一 run ID。
- 该 run 的 `verified_count=1`、`final_fields_source=detail_page`、`extraction_methods={"json_ld":1}`，字段证据为 `title,company,location,employment_type,requirements,description`，哈希长度 64。
- 用户级 `DASHSCOPE_API_KEY` 存在，但对 DashScope `/models` 的真实请求返回 `invalid_api_key`；没有写入日志或仓库，也没有声称 Qwen 在线调用成功。
