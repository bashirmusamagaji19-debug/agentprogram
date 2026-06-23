# 2026-06-23 Real Site Sample 4-Run

## 目标

把 `--evaluate --real-site-sample` 和 `--compare-llm-extractor --real-site-sample` 扩展到 4 条真实站点样本，确认 baseline、LLM demo 和 DeepSeek provider 的差异。

## 过程

1. 用现有 `HttpPageLoader` 跑 4 条真实站点样本评测。
2. 跑同一批样本的 `--compare-llm-extractor`，并启用 `--llm-extractor-provider deepseek`。
3. 回查 JSON 和 Markdown 报告，确认每条样本的成功/失败归因。

## 结果

- `--evaluate --real-site-sample --evaluation-count 4` 成功，`Completed tasks: 3/4`，`verification_filtered=1`。
- `--compare-llm-extractor --real-site-sample --evaluation-count 4 --llm-extractor-provider deepseek` 成功。
- 对比结果：`baseline: 3/4`、`llm-demo: 3/4`、`deepseek: 4/4`。
- 失败样本是 `Applied AI Claude Evangelist`，baseline 和 demo 都是 `jobs_found=1; valid_jobs=0`，DeepSeek 通过验证。

## 说明

- 真实站点样本模式已经从 smoke 级别推进到 4 样本 benchmark。
- 当前差异表明，规则抽取和 deterministic demo 在这条 Anthropic 样本上会被验证器过滤，DeepSeek provider 能补上这一条。
- 这次补了过滤原因回填，后续 `evaluation.json` 和评测报告都能直接看到被过滤岗位的具体理由。
- `verification_filtered` 现在会记录 `title/company/reasons`，方便后续复盘到底是规则、置信度还是相关性触发了过滤。
- 这轮的产出已经足够做面试叙述：可验证工作流、4 样本 benchmark、过滤原因回填、DeepSeek/Qwen provider 对比。
- README 现在已经直接链接到 `docs/interview-benchmark-story.md`，便于快速复述项目亮点。

## 验证

- `.\.venv\Scripts\web-task-agent.exe --evaluate --real-site-sample --evaluation-count 4 --json-output evaluations\real-site-4.json`
- `.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --real-site-sample --evaluation-count 4 --llm-extractor-provider deepseek --json-output evaluations\real-site-4-compare.json`
- `pytest -q tests/test_browser.py tests/test_scaffold.py`
