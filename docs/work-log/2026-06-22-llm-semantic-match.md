# 本轮工作：LLM 语义匹配

## 目标

把匹配模块从纯规则匹配升级为规则优先 + LLM 语义 fallback 的两层架构，让低匹配度岗位能通过语义理解发现隐藏的匹配点（例如岗位写 "FastAPI" 但简历写 "API 开发"）。

## 为什么需要这个改动

纯规则匹配的问题是**只能做字面比较**，无法理解语义等价：

- 岗位要求 "FastAPI"，简历写 "built RESTful APIs with Python" → 规则认为不匹配
- 岗位要求 "browser automation"，简历写 "Playwright script for web scraping" → 规则认为不匹配

这些案例在字面上完全不同，但语义上高度相关。LLM 语义匹配可以识别这种关系。

但规则匹配也有优势：**快、免费、可测试、结果稳定**。所以不应该直接替代规则匹配，而是：
1. 规则匹配先行，分数高的直接返回（快速路径）
2. 规则匹配分数低时，尝试 LLM 语义匹配（深度路径）
3. LLM 调用失败时，静默回退到规则结果（降级路径）

## 改动内容

### llm_extractor.py

- 新增 `LlmMatcher` 类型别名：`Callable[[dict], dict]`
- 新增 `OpenAiCompatibleSemanticMatcher`：复用已有的 provider 配置体系（DeepSeek/Qwen），通过 OpenAI-compatible API 做语义匹配，prompt 设计包含岗位全貌（标题、公司、要求、职责、技能）和用户画像（技能、简历）
- 新增 `DemoLlmMatcher`：确定性 demo 实现，做 skill overlap 计算并生成中文解释，不调真实 API
- 新增 `build_configured_llm_matcher()` 工厂函数，与 `build_configured_llm_field_extractor()` 对称

### matcher.py

- `JobMatcher.__init__` 接受可选 `llm_matcher` 和 `llm_min_rule_score`（默认 0.6）
- `match()` 方法改为三层策略：
  1. 规则匹配
  2. 如果 rule score < 0.6 且有 llm_matcher → 调用 LLM
  3. LLM 调用失败 → 静默回退到规则结果
- 新增 `_rule_match()` 私有方法，抽取纯规则匹配逻辑
- 新增 `_str_list()` helper，安全处理 LLM 返回的字符串列表

### cli.py

- 新增 `--llm-match`：启用 LLM 语义匹配，不指定 provider 时默认用 demo
- 新增 `--llm-match-demo`：显式使用 deterministic demo
- 新增 `--llm-match-provider deepseek|qwen`：使用真实 LLM
- 新增 `--llm-match-model`：覆盖默认模型
- 新增 `build_cli_llm_matcher()` 辅助函数
- `build_workflow()` 接受并传递 `llm_matcher`
- 输出会打印 `LLM match enabled: llm-match-demo` 或 provider 名称

### JSON metadata 记录

metadata 中记录 `llm_match_mode`，方便事后区分哪些 run 用了 LLM 匹配。

## 验证记录

```powershell
# 全部测试
.\.venv\Scripts\python.exe -m pytest -q
# 结果：全部通过

# 验证 LLM match 的 demo 路径可用
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --target-count 2 --skill Python --skill LangGraph --demo --llm-match --json-output outputs\result-llm-match.json
# 结果：LLM match enabled: llm-match-demo，Valid jobs: 2
```

新增测试：
- `test_demo_llm_matcher_computes_skill_overlap`：验证 DemoLlmMatcher 的 skill overlap 计算
- `test_demo_llm_matcher_high_match_when_all_skills_present`：全匹配场景
- `test_demo_llm_matcher_zero_match_when_no_overlap`：零匹配场景
- `test_matcher_falls_back_to_llm_when_rule_score_low`：验证 LLM fallback 触发
- `test_matcher_skips_llm_when_rule_score_high`：验证高分时不触发 LLM
- `test_matcher_falls_back_to_rule_when_llm_errors`：验证 LLM 失败时降级到规则结果

## 结果解释

现在匹配模块有三条路径，面试时可以讲清楚：

1. **快速路径（规则匹配）**：字面技能匹配，0 成本、可测试、结果稳定。适合大部分场景。
2. **深度路径（LLM 语义匹配）**：当字面匹配度低时，LLM 可以识别语义等价。适合简历和 JD 用不同措辞描述相同能力的场景。
3. **降级路径**：LLM 调用失败时静默回退到规则结果，不中断工作流。

这个设计体现了**工程权衡**：不是"规则 vs LLM"的二选一，而是按场景分层的策略。

## 当前限制

- LLM 匹配的 prompt 目前只发送技能和简历文本，没有发送项目经历的详细描述
- 语义匹配还没有批量评测（需要用真实 provider 对一批 JD 跑分再人工校验）
- DemoLlmMatcher 仍然是确定性算法（skill overlap），不是真正的 LLM

## 下一步

- 增加 LLM 语义匹配的批量评测：同一批岗位分别跑规则匹配和 LLM 匹配，人工标注匹配质量，输出对比报告
- 可选：增加 `--llm-match-min-score` 参数，允许用户自定义触发阈值
- 可选：让 DemoLlmMatcher 也支持简历文本中的隐含技能提取
