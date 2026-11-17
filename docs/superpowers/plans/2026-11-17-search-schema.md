# 搜索分页与模型输出约束实施计划

> **For agentic workers:** 按任务逐步实施；本次在当前会话直接执行，保留工作区已有修改，不创建提交。

**Goal:** 自动补充搜索候选，严格校验模型输出，并在输出异常时有界补偿。

**Architecture:** BrowserUseClient 管理每个查询的分页游标、跨页去重与截止条件；SearchJobsTool 透传分页诊断，策略在候选用尽后继续发现。独立 structured_output 模块统一生成 Schema、检查响应信封和严格校验，抽取/匹配/阅读器至多补偿一次；Planner 的每次请求仍经过预算与遥测，失败后由现有确定性策略接管。

**Tech Stack:** Python 3.11、Pydantic 2、pytest、OpenAI-compatible Chat Completions。

### Task 1: 自动分页

- [x] 在 tests/test_search_pagination.py 先覆盖跨页去重、达到数量即停、重复页/空页停止、页数上限、续搜、后页失败保留结果和截止时间。
- [x] 运行 `.venv/Scripts/python -m pytest tests/test_search_pagination.py` 确认失败。
- [x] 修改 browser.py：每个查询独立保存游标与已发现 URL，优先读取同查询的下一页链接，缺失时使用 start 偏移；统一输出 search_diagnostics。
- [x] 修改 agent_tools.py、agent_policy.py：记录候选缺口，候选耗尽且搜索额度允许时续搜；不足时保留已验证结果。
- [x] 运行分页和 Agent 策略/工具/运行时回归。

### Task 2: Schema 与有界补偿

- [x] 在 tests/test_structured_output.py 先覆盖多余字段、缺失字段、错误类型、非法分数/枚举、截断、拒绝、空输出、一次修复和修复耗尽。
- [x] 运行该测试确认失败。
- [x] 新增 structured_output.py：严格 Pydantic 输出模型、严格 JSON 解析、响应错误分类、Schema 请求配置与不含原始输出的修复提示。
- [x] 将 llm_extractor.py、agent_planner.py、evidence_reader.py 接入。Schema 原生能力按供应商/模型选择；JSON Object 兼容路径同样必须通过完整本地 Schema 校验，不放宽校验。
- [x] Planner 每次补偿调用各自记账；抽取耗尽后保留规则结果，匹配保留规则评分，阅读器返回明确错误。
- [x] 更新旧测试中依赖宽松转换的预期及模拟响应，运行相关回归。

### Task 3: 文档与验证

- [x] README 说明分页限额、候选数量和交付数量区别、Schema 能力与补偿规则。
- [x] `.venv/Scripts/python -m ruff check src tests`。
- [x] `.venv/Scripts/python -m pytest -p no:cacheprovider --cov=web_task_agent --cov-report=term-missing --cov-fail-under=70`。
- [x] 检查最终差异，保留其他已有改动。

### 验证结果（2026-11-17）

- 新增分页与 Schema 回归测试 37 项，包含候选拒绝后续搜并达到目标的端到端流程。
- 全量测试：1182 passed，1 skipped；覆盖率 88.30%，满足 70% 门槛。
- Ruff：All checks passed。
- git diff --check：通过。
- 采用本地受控页面和模拟模型响应；未对真实 Google/招聘网站或真实模型服务执行在线验收。
- 保留工作区已有改动，未创建提交。
