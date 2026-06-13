# 本轮工作：LLM provider 接入与行动计划增强

## 目标

把 Web Task Agent 从 deterministic LLM demo 扩展到可配置的真实 LLM provider，同时把行动计划产物改成更适合面试讲述的材料。

## 改动内容

- 在 `src/web_task_agent/llm_extractor.py` 增加 OpenAI-compatible LLM 抽取器。
- 支持 `deepseek` 和 `qwen` 两个 provider：
  - DeepSeek 默认模型：`deepseek-v4-flash`，读取 `DEEPSEEK_API_KEY`。
  - Qwen 默认模型：`qwen-plus`，读取 `DASHSCOPE_API_KEY`。
- 在 CLI 中增加：
  - `--llm-extractor-provider deepseek|qwen`
  - `--llm-extractor-model <model>`
- 保持规则抽取优先，只有低置信度页面才通过 `PageExtractor` 调用 LLM 抽取边界。
- JSON 输出会记录 `extractor_mode`、`llm_provider` 和 `llm_model`。
- 行动计划新增 `技术栈体验与面试说法`，把 browser-use、LangGraph、评测和补强任务转化为面试叙事。
- 更新 `README.md`、`docs/project-story.md` 和 `docs/mvp-verification.md`。

## 验证记录

执行过的主要命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_llm_extractor.py tests\test_scaffold.py tests\test_evaluation.py tests\test_extractor.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

缺少 DeepSeek key 的错误路径也做过 smoke：

```powershell
$env:DEEPSEEK_API_KEY=''
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/unstructured-ai-agent-intern" --demo --target-count 1 --llm-extractor-provider deepseek --json-output outputs\missing-key-check.json
```

预期输出包含：

```text
LLM extractor is not configured: DEEPSEEK_API_KEY is required for deepseek LLM extraction.
```

在存在 DeepSeek key 的环境中，provider smoke 可生成 JSON，并记录：

```text
extractor_mode=llm-provider
llm_provider=deepseek
llm_model=deepseek-v4-flash
valid_jobs=1
```

## 结果解释

这个改动让项目可以讲清楚三层抽取策略：

1. 规则抽取优先，保证可测试和低成本。
2. deterministic LLM demo 用于稳定展示低结构化 JD 的恢复能力。
3. DeepSeek/Qwen provider 用于真实模型抽取，并通过环境变量和模型参数切换。

面试中可以强调：模型不是直接硬编码进 workflow，而是通过 `LlmFieldExtractor` 边界注入；测试使用 fake transport，不依赖真实 API key；运行产物会记录 provider 和 model，方便后续做模型效果对比。

## 当前限制

- 真实 provider 目前只在单条 unstructured seed demo 上做了 smoke，还没有形成批量评测集。
- provider 输出只做 JSON 解析和 `JobPosting` 间接校验，还没有字段级质量评分。
- Qwen provider 已接入配置和命令路径，但当前机器还没有 `DASHSCOPE_API_KEY`。

## 下一步

下一轮建议实现真实站点 seed URL 评测和 LLM provider 对比：

- 准备一组真实或真实风格招聘 URL。
- 同一批页面分别跑规则抽取、deterministic demo、DeepSeek 和 Qwen。
- 输出 Markdown + JSON 评测结果，比较成功率、字段完整度和失败原因。
- 同步维护 `docs/work-log/`、`docs/project-story.md` 和 `docs/mvp-verification.md`。
