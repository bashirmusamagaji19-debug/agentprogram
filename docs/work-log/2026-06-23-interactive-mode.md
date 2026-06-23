# 本轮工作：多轮 Agent 对话 — --interactive 模式

## 目标

此前 Agent 是单次任务：输入关键词 → 输出报告，无反馈循环。真正的 Agent 应该能接受反馈、调整策略、多轮迭代。

这个改动增加 `--interactive` CLI 模式，支持 REPL 循环：用户看到结果后可以调整搜索参数重新搜索，所有轮次结果去重合并，最终生成合并报告。

## 为什么需要这个

面试时可以说：

> "Agent 不是一次性脚本。我加了一个 interactive 模式——用户看到第一轮结果后，可以说 'more' 扩大搜索范围、'skill:X' 加技能标签、'keyword:X' 改方向。每轮都独立执行并去重合并。这展示了 Agent 的反馈循环能力。"

## 改动内容

### cli.py

- 新增 `--interactive` CLI flag
- 新增 `run_interactive()` 异步函数，实现 REPL 循环：
  - 每轮：构建 UserProfile → 运行 workflow → 展示结果 → 等用户输入
  - 支持命令：`more[:N]`、`skill:X`、`keyword:X`、`location:X`、`status`、`done`
  - 所有轮次的岗位按 URL 去重合并
  - 结束后生成合并报告（复用 reporter/dashboard/action-plan 管线）
  - metadata 记录交互轮次数
- 新增 `RunMetrics` import

### tests/test_scaffold.py

- 新增 `test_cli_interactive_runs_multiple_rounds_and_consolidates`：
  用 `_FakeInput` 模拟用户输入 "skill:FastAPI" → "more:3" → "done"，
  验证 3 轮运行 + 验证报告/JSON/Dashboard 产物存在
- 新增 `_FakeInput` helper 类

## 验证

```powershell
# 单轮
printf "done\n" | .\.venv\Scripts\web-task-agent.exe --interactive --keyword "AI intern" --target-count 2 --skill Python --demo --dashboard --action-plan --json-output outputs\interactive-test.json

# 输出：
# Round 1: 2 valid / 2 total
# Consolidated report: reports\interactive-consolidated-xxx.md
# Action plan + Dashboard + JSON all produced
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_scaffold.py::test_cli_interactive_runs_multiple_rounds_and_consolidates -q
```

## 面试说法

> "我做的不是一次性搜索脚本。Agent 有一个 interactive 模式——跑完第一轮，用户可以说 'more' 扩大范围、'skill:FastAPI' 加标签、'keyword:X' 换方向。每轮独立运行，结果自动去重合并，最后生成合并报告。这个模式展示了 Agent 的核心循环：执行 → 反馈 → 调整 → 再执行。"

## 当前限制

- REPL 是简单的 `input()` 循环，不是 LangGraph 的 human-in-the-loop checkpointer（后者更适合生产，前者更适合演示）
- 去重只按 URL，未按 (title, company) 复合键
- 暂时不支持 `seed_url` 的交互式添加（seed URL 通常是固定集合）
