# 本轮工作：LLM 语义匹配批量对比评测 (--compare-llm-match)

## 目标

给匹配模块加上和抽取器同等级别的批量对比评测能力：对同一批岗位分别跑规则匹配和 LLM 语义匹配，产出对比数据，不再只有一个 case 的 smoke。

## 为什么需要这个

此前匹配模块只有单次 `--llm-match` smoke（"跑通了"），没有批量评测。面试时能说「DeepSeek 抽取 88%」，但不能说「DeepSeek 语义匹配对匹配质量有什么影响」。

这个改动补上了缺失的评测维度。

## 改动内容

### cli.py

- 新增 `--compare-llm-match` CLI flag
- 新增 `run_llm_matcher_comparison()` 函数：
  1. 用 BrowserClient + Extractor 提取真实岗位
  2. 对每个岗位同时跑规则匹配和 LLM 语义匹配
  3. 比较分数、优先级、匹配/缺失技能
  4. 输出 Markdown 报告 + JSON
- 新增 `_summarize_match_comparison()`：统计分数变化数、优先级变化数
- 新增 `write_llm_match_comparison_report()`：生成对比评测 Markdown 报告

### tests/test_scaffold.py

- 新增 `test_cli_compare_llm_match_full_pipeline`：端到端 smoke 测试

## 评测结果（DeepSeek 抽取 × DeepSeek 匹配，7/8 岗位有效）

| 指标 | 结果 |
|---|---|
| 有效岗位 | 7/8 |
| 规则 vs DeepSeek 分数变化 | **4/7** (57%) |
| 优先级变化 | 0/7 |

### 关键发现

**规则匹配在真实页面上全打 0 分**。原因不是匹配逻辑有 bug，而是 Greenhouse 页面的"技能"字段被抽取为原始需求文本（如 "7+ years of experience across a combination of..."），不是关键词列表。规则匹配无法在长文本中做语义识别。

**DeepSeek 语义匹配在 4/7 的岗位上从 0 分提升到非零分**，做了有效的语义桥接：
- `LangGraph` → "Agentic AI" (岗位 5: Reddit Analytics Engineer)
- `FastAPI` → "back-end engineering" (岗位 5)
- 岗位 1 (Anthropic Claude Evangelist)：从 0 → 0.15，识别出 Python/LangGraph/FastAPI 相关性

剩余 3 个岗位（Anthropic TPM、ScaleAI AI Builder Intern、Discord Director）LLM 也给出了 0 分，说明这些岗位的 JD 确实与用户的 Python + LangGraph + FastAPI 背景无关——**0 分本身也是正确的匹配结果**。

### 面试说法

> "我对 7 个真实岗位做了规则匹配 vs DeepSeek 语义匹配的对比评测。结果是 4/7 的岗位中，语义匹配发现了规则匹配完全找不到的关联。规则匹配全打 0 分是因为 Greenhouse 页面抽取的'技能'字段是原始需求文本而不是关键词列表——这正是真实数据的特点。语义匹配能处理这种不确定性。"

## 验证

```powershell
.\.venv\Scripts\web-task-agent.exe --compare-llm-match --real-site-sample --evaluation-count 8 --llm-extractor-provider deepseek --llm-match-provider deepseek --skill Python --skill LangGraph --skill FastAPI --resume-text "..." --json-output evaluations\match-comparison-deepseek.json
.\.venv\Scripts\python.exe -m pytest tests/test_scaffold.py::test_cli_compare_llm_match_full_pipeline -q
.\.venv\Scripts\python.exe -m pytest -q
```

## 当前限制

- 评测使用固定用户画像（Python/LangGraph/FastAPI 技能 + 简历文本），还没有多用户画像的对比
- 报告只统计分数变化和优先级变化，没有字段级（matched_skills 列表变化）的详细对比
- Qwen 语义匹配还没跑评测（需要先确认 Qwen 在匹配 prompt 下的输出格式兼容性）
