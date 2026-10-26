# 留给你的人工任务

**最新状态（2026-10-25）：用户已完成14条复核并确认两项处理口径，修订冻结版本为 model-value-v4，回执校验已通过，不需要重填下述旧 v3 模板。正式运行使用 evaluations/model-value-v4。用户已确认 deepseek-chat；两套正式实验共720单元和10条真实来源任务已运行。真实交付0条，目前无需逐岗确认；可选核查 docs/results/formal-model-value-v4-20261025/EXTERNAL_REVIEW.md 的3份失败样本。下文保留初始复核流程作为参考。**


本轮代码修复、离线回归和评测工具由 Agent 完成。以下事实判断需要你亲自确认；没有完成前，正式模型收益结论保持待验证。

当前工作目录是 `C:\Users\13993\Desktop\大模型学习\Agent\.worktrees\agent-improvements`。代码仍在这个隔离工作区，原始 `Agent` 目录没有被覆盖；以下命令请先切换到这个目录。

## 1. 复核冻结标签（必须）

使用 `evaluations/model-value-v3/REVIEW.md`，不要沿用旧 v1/v2 的复核回执。先阅读各案例的原文，再看 proposed label，避免先看模型输出。

至少复核 12/60 条，dev 和 test 各至少 4 条，并且两个 split 都包含正常和边界案例。建议直接复核 REVIEW.md 中列出的全部案例。每条确认：

- 标题、公司、地点和实习性质是否有原文支持；引用能否逐字找到。
- gold job key 是否对应同一个业务岗位，重复项有没有重复计算。
- 应当交付、拒绝、保守 review、候选不足或故障终止，是否与实际源内容及受控故障一致。
- 有歧义就记录 uncertain 和理由，不要为了通过门禁点确认。

将 `docs/results/audit-fix-2026-10-25/HUMAN_REVIEW_RECEIPT.template.json` 复制到 `evaluations/model-value-v3/HUMAN_REVIEW_RECEIPT.json`。只在真正审完后填入你的姓名/称呼、带时区时间、实际审过的 case IDs、依据文件和说明，并将 status 改成 complete。模板不等于已复核回执。

如果发现标签错误，填入 changed_labels 并另附修订理由；不要直接改冻结的 labels.jsonl。此时门禁继续拒绝是正确行为，需要据你的反馈生成下一个冻结版本，再绑定新回执。

验证命令（在本次隔离工作区运行）：

```powershell
Set-Location -LiteralPath 'C:\Users\13993\Desktop\大模型学习\Agent\.worktrees\agent-improvements'
$py = 'C:\Users\13993\Desktop\大模型学习\Agent\.venv\Scripts\python.exe'
& $py scripts/run_model_value_benchmark.py --suite planner --dataset evaluations/model-value-v3 --split test --validate-only
```

## 2. 核对真实任务交付（必须，但与合成标签分开）

查看本轮真实来源运行和后续外部任务的归档报告。逐条对照原始正文，确认标题、公司、地点、实习性质及引用是否支持交付；无法确认当前开放时只记录“采集时观察到的正文”，不要写“岗位仍在招”。失败/空结果同样记录，不补成成功。

三轮旧采集只覆盖约一分钟；本轮新采集不是跨时段长期稳定性证明。若要在面试中声称长期稳定，需要在另一个真实时段重跑，而不能复制已有结果。

## 人工回执之后的机器工作

正式 test 40 条 × 三策略 × 三轮、分别执行 Planner 和 Reader，是机器任务，依赖第 1 项回执，不要求你手工逐次执行。使用配套 `scripts/finish_model_value_evaluation.ps1`；它先校验回执和 provider/model，再运行固定配对实验，保留失败和未完成项。模型 ID 必须显式传入，Key 保留在环境变量中，不填写进文档。该实验会产生真实 API 费用，按计划执行调用与 Token 上限。

例如（在项目隔离工作区运行，模型 ID 使用你部署配置中的明确值）：

```powershell
./scripts/finish_model_value_evaluation.ps1 -Python $py -Provider deepseek -Model $env:PLANNER_MODEL
```

脚本随后自动采集 10 条预先声明的真实任务：北京/上海 × AI、大模型、Agent、RAG、算法实习，限定腾讯校招与小米两个来源、规则策略、每任务 45 秒。这只是小规模外部验证，仍需你按第 2 项核对交付；空结果或源故障不会自动变成成功。

这批正式结果出来后再做默认策略决策。当前保持 rules 默认；开发 pilot、合成回归和真实来源故障记录不能替代正式收益结论。
