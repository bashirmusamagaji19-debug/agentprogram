# 模型按需介入决策

日期：2026-10-23

## 结论

本轮开发 pilot 支持保留 `rules` 作为生产默认策略。模型策略在 6 条开发任务上没有提高完成质量，却增加了调用、Token 和延迟；`adaptive` 也尚未证明优于规则。正式 test 与生产参数冻结尚未执行，因为 `model-value-v2` 仍缺少外部人工复核回执。

因此：

- 生产默认：`rules`。
- `adaptive`：保留为显式实验选项，仅在后续经过人工复核的难例类别中证明有收益后再路由。
- `always`：不作为默认；本轮 Reader 的保守 review 还降低了交付完成率。
- Matcher：按预注册计划继续使用规则默认，本轮不以 Planner/Reader 结果替代排序收益证据。

## 数据与模型

- 数据集：`evaluations/model-value-v2`，60 条任务、30 个独立基础组、20 条 dev、40 条 test。
- 标签状态：`pending_human_review`；没有写入或伪造 `HUMAN_REVIEW_RECEIPT.json`。
- v1 已知语义标签错误，保留审计，不用于正式推断；v2 是独立修订版本。
- Pilot：前 6 条 dev 任务，每条任务的三策略配对 1 轮，共 3 个独立组。
- Provider/model：DeepSeek `deepseek-chat`；服务端响应模型标识为 `deepseek-flash`。
- 参数：temperature 0，JSON object 输出；Planner 每 case 最多 4 次调用，单 case 最长 60 秒。
- 预算：每 suite 最多 60 次真实请求、200,000 观测 Token。

## Planner 结果

| 策略 | 完成率 | 精确率 | 覆盖率 | 模型调用 | 观测 Token | P50 | P95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| rules | 6/6 | 1.00 | 1.00 | 0 | 0 | 31.7 ms | 51.9 ms |
| adaptive | 6/6 | 1.00 | 1.00 | 4 | 2,283 | 1,252.4 ms | 1,556.3 ms |
| always | 6/6 | 1.00 | 1.00 | 24 | 11,784 | 4,327.3 ms | 5,393.1 ms |

三策略 paired bootstrap 的完成率差均为 0，95% 区间为 `[0, 0]`。这是只有 3 个独立组的小样本，不能外推为总体等效。`always` 发生了 6 次非法动作回退，但运行时未越权且任务仍完成。

## Reader 结果

| 策略 | 完成率 | 精确率 | 覆盖率 | 任务 Review 率（历史口径） | 模型调用 | 观测 Token | P50 | P95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| rules | 6/6 | 1.00 | 1.00 | 0.00 | 0 | 0 | 5.6 ms | 7.5 ms |
| adaptive | 6/6 | 1.00 | 1.00 | 0.00 | 0 | 0 | 5.4 ms | 6.1 ms |
| always | 4/6 | 1.00 | 0.67 | 0.67 | 14 | 19,559 | 4,314.2 ms | 4,915.2 ms |

`always` 有 2 个正确岗位漏交付，主要来自模型报告的保守 review；没有证据支持全量模型阅读。`adaptive` 在这 6 条任务上全部由规则直接接受，没有触发模型升级，因此本轮仍不能证明 adaptive 的模型分支有质量收益。

## 修复与失败证据

- 修复 dataset 根目录与嵌套 run archive 根目录混用导致的 `ArchiveNotFoundError`。
- 修复候选 URL 与排序后的 `snapshot_refs` 错位配对，改为 `snapshot_by_url` 显式映射。
- 修复 Reader 对跨来源同一 `job_key` 的重复业务写入；去重后的正式 pilot 中 `duplicate_writes=0`。
- 修复 Windows 260 字符边界下的 SourceArchive 原子写入与读取；长路径回归覆盖 body、snapshot 写入及读回。
- Qwen pilot 因 DashScope 返回 `invalid_api_key` 全部回退，该轮保留为失败证据，不参与模型质量比较。
- 旧 Reader pilot 暴露重复写入；保留原产物，最终指标使用去重后的重跑结果。

## 来源观察与离线回放

- 三轮真实官方来源观察均保留：每轮 3/3 来源可调用、3/3 返回候选、7/7 快照哈希完整，各成功打开 7 次、失败 6 次，最终为 failed/budget_exhausted、交付 0 条；不是全部详情 removed。
- Demo 归档验证通过：6 candidates、6 snapshots、6 attempts。
- `content` 与 `attempts` 回放均恢复 3 条并保存 3 条，未访问搜索、原站或实时模型。
- 对真实第 1 轮归档执行 attempts 回放可复现 0 条交付和失败终态。
- 篡改正文的归档被 `ArchiveCorruptionError` 拒绝。

## 正式执行状态

正式 test：`not_run_missing_human_review`。

`evaluations/model-value-v2/REVIEW.md` 是待人工审阅包。外部审阅者需要按 `PROTOCOL.md` 写真实回执；在回执存在且覆盖要求满足前，不运行 40 条 test × 3 策略 × 3 轮的正式对照，也不发布正式模型收益数字。

## 证据位置

- Planner pilot：`portfolio-artifacts/model-value-v2-pilot-deepseek-20261023`
- Reader 最终 pilot：`portfolio-artifacts/model-value-v2-reader-pilot-deepseek-20261023-dedup`
- Reader 重复写入失败证据：`portfolio-artifacts/model-value-v2-reader-pilot-deepseek-20261023`
- Qwen 无效 Key 证据：`portfolio-artifacts/model-value-v2-pilot-20261023`
- 离线目标 3：`portfolio-artifacts/acceptance-20261023-target3`
- 目标不足：`portfolio-artifacts/acceptance-20261023-partial`
- HITL 批准：`portfolio-artifacts/acceptance-20261023-hitl-approve-final2`
- HITL 拒绝：`portfolio-artifacts/acceptance-20261023-hitl-reject`
- 内容回放：`portfolio-artifacts/source-replay-20261023-content`
- 尝试回放：`portfolio-artifacts/source-replay-20261023-attempts`
- 三轮在线观察：`portfolio-artifacts/source-capture-20261023-r1` 至 `r3`
- 损坏归档反例：`portfolio-artifacts/source-corruption-case-20261023`

## 剩余不确定性

- 当前 pilot 只有 6 条开发任务和 3 个独立组，不足以决定难例类别级路由。
- v2 标签尚未人工复核，不能把开发 pilot 当作正式 gold 结论。
- 三轮在线观察只覆盖约一分钟，且预算耗尽，不能据此推断岗位市场或长期来源稳定性。
- 模型服务端实际响应模型与请求别名不同，正式记录必须继续保存两者。

## 2026-10-25 审核修订

旧原始产物完整保留。Reader always 的任务 Review 为 4/6=66.67%，按协议页面口径为 10/14=71.43%；不可混用。预算恢复、失败交付计分和冻结标签的问题在本轮修复，新正式评测应使用新冻结版本并经人工复核。以上 pilot 仅为历史开发观察，不作为修复后正式模型收益结论。
