# 链接交付校验验收（2026-10-26）

本次完成的是“未经实际页面核验的链接被计入交付”这一漏洞修复。它不表示全部 42 个发现源都已修通；来源覆盖情况仍以 2026-10-25 全量审计为准。

## 交付规则

岗位必须在本次运行中由独立浏览器访问其显示链接，确认实际地址、完整岗位身份、对应标题与 JD，以及可见且启用的投递入口。静态 HTTP、官方 API 或列表正文均不能单独证明网页可投递。

核验记录绑定本次 run_id，保存前检查记录不超过 15 分钟；缺失、过期、上一轮记录或未确认身份均不交付。人工确认保存也不能绕过这道检查。明确下线排除，挑战、空页、无法取得最终地址等进入未交付候选。

实际保存失败或超时尚未入库时，已核验岗位仍不算已交付；UI、报告和 CLI 导出均保留未交付原因。旧历史没有核验记录时显示未核验，不补造证明。历史核验仅表示检查当时的页面状态，不是永久有效保证。

## 实际网页验收

彻底重启本地 Streamlit 以加载最终模块后，通过 Edge 中的真实表单启动任务：

- 运行 ID：`run-3de02cbd`。
- 数据模式：官方实时来源 `xiaomi`；关键词 `AI Agent 实习`；全国；目标 3。
- 测试技能：`Python, LangGraph`；未上传简历，未启用模型。这是链接与保存验收，匹配分不是对用户真实简历的评估。
- 发现 20，选定 10，实际检查 3，独立页面核验 3，数据库保存 3；待核验 0；未检查 17。
- 三个结果均有 `method=browser` 的记录、公开正文证据、实际地址和可见启用投递控件。
- 已核对三个证据文件的 SHA-256 均与记录一致，SQLite 有 3 行，下载 ZIP 含三个公开网页证据文件。
- 浏览器未观察到 HTTP 响应状态时记录 `null`，不再猜测为 200。

| 岗位 | 地点 | 实际核验并交付的链接 |
|---|---|---|
| agent应用实习生-2027届 | 北京 | [官方详情](https://xiaomi.jobs.f.mioffice.cn/internship/position/7662271237599725834/detail) |
| AI 开发工程师（Agent 方向）-2027届 | 武汉 | [官方详情](https://xiaomi.jobs.f.mioffice.cn/internship/position/7698943066581584191/detail) |
| Agent评估方向算法实习生 | 北京 | [官方详情](https://xiaomi.jobs.f.mioffice.cn/internship/position/7662657793955268910/detail) |

“交付”指保存可供人工核对的岗位结果，没有提交求职申请。页面核验不替代届别、到岗时间等资格核对。

## 修复过程中真实暴露的问题

首次普通 HTTP 访问上述详情页返回 404，而正常浏览器可以显示岗位。产品现在允许浏览器进一步确认，不能仅凭此类静态 404 推断岗位消失。独立浏览器原先仅等 0.75 秒，早于 SPA 首次渲染；默认等待改为最多 8 秒，正文出现即结束，仍受总体时限约束。

独立审查还收紧了岗位 ID 完整保留（不能只匹配年份）、静态 HTML 无法证明 CSS 可见性、未入库不计交付，以及未知 HTTP 状态的标注。

## 验证证据

- 全量：`1105 passed, 1 skipped in 218.75s`，日志 `portfolio-artifacts/link-delivery-20261026/release-regression.log`。
- Ruff：`All checks passed!`，日志 `ruff-release.log`。
- Wheel：成功生成 `web_task_agent-0.1.0-py3-none-any.whl`，日志 `wheel-release.log`。
- Doctor：Python 虚拟环境、LangGraph、browser-use、Pydantic 和输出位置检查通过。
- `git diff --check` 无空白错误。
- 最终 UI 机器证据：[final-ui-evidence.json](final-ui-evidence.json)。
- 公开网页证据包：[link-checks.zip](link-checks.zip)。
- 截图：本地 `portfolio-artifacts/link-delivery-20261026/website-final.jpg`。
- 原始运行：本地 `streamlit-runs/run-3de02cbd/`，同时出现在正常网站历史中。

本地入口为 [http://127.0.0.1:8510](http://127.0.0.1:8510)，健康检查 200。此服务仍运行于当前 worktree；清理或迁移前应先停止它。此次没有修改模型密钥或配置，也没有外发简历。
