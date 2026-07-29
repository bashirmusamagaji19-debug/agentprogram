# 真实 Planner 对照评测

- Benchmark version: `hybrid-agent-planner-controlled-v1`
- Date: `2026-07-29`
- Scope: `controlled replayable runtime scenarios`

本评测让 deterministic、DeepSeek 和 Qwen 在同一批受控、可复现的运行时场景中决策。
它衡量 Planner 决策、授权 fallback 与循环终止，不代表真实招聘网页的抽取泛化能力。

## Provider 对照

| Provider | Model | 状态 | 任务完成率 | 循环终止率 | 工具成功率 | Fallback 率 | 非法决策率 | 平均步数 | Planner 延迟 ms | Total Token |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| deterministic | deterministic-policy-v1 | executed | 4/5 (80.00%) | 5/5 (100.00%) | 87.50% | 0.00% | 0.00% | 3.80 | 0.00 | 0 |
| deepseek | deepseek-v4-flash | executed | 4/5 (80.00%) | 5/5 (100.00%) | 90.91% | 33.33% | 33.33% | 3.40 | 50369.77 | 5518 |
| qwen | qwen-plus | executed | 4/5 (80.00%) | 5/5 (100.00%) | 100.00% | 0.00% | 0.00% | 3.20 | 28096.37 | 5077 |

## 场景明细

| Provider | Case | 终态 | 终止原因 | 完成 | 步数 | Planner calls | Fallback | 动作序列 | 决策来源 |
|---|---|---|---|---|---:|---:|---:|---|---|
| deterministic | seed-happy-path | completed | target_reached | yes | 3 | 0 | 0 | open_page -> extract_text -> verify_job -> finish | policy -> policy -> policy -> policy |
| deterministic | search-happy-path | completed | target_reached | yes | 4 | 0 | 0 | search_jobs -> open_page -> extract_text -> verify_job -> finish | policy -> policy -> policy -> policy -> policy |
| deterministic | open-recovery | completed | target_reached | yes | 5 | 0 | 0 | open_page -> open_page -> open_page -> extract_text -> verify_job -> finish | policy -> policy -> policy -> policy -> policy -> policy |
| deterministic | verifier-recovery | completed | target_reached | yes | 6 | 0 | 0 | open_page -> extract_text -> verify_job -> open_page -> extract_text -> verify_job -> finish | policy -> policy -> policy -> policy -> policy -> policy -> policy |
| deterministic | budget-exhaustion | partial | budget_exhausted | no | 1 | 0 | 0 | open_page -> finish | policy -> policy |
| deepseek | seed-happy-path | completed | target_reached | yes | 3 | 3 | 1 | open_page -> extract_text -> verify_job -> finish | llm -> llm -> fallback -> policy |
| deepseek | search-happy-path | completed | target_reached | yes | 4 | 4 | 2 | search_jobs -> open_page -> extract_text -> verify_job -> finish | llm -> fallback -> fallback -> llm -> policy |
| deepseek | open-recovery | completed | target_reached | yes | 5 | 3 | 1 | open_page -> open_page -> open_page -> extract_text -> verify_job -> finish | fallback -> policy -> policy -> llm -> llm -> policy |
| deepseek | verifier-recovery | completed | target_reached | yes | 4 | 4 | 1 | open_page -> open_page -> extract_text -> verify_job -> finish | fallback -> llm -> llm -> llm -> policy |
| deepseek | budget-exhaustion | partial | budget_exhausted | no | 1 | 1 | 0 | open_page -> finish | llm -> policy |
| qwen | seed-happy-path | completed | target_reached | yes | 3 | 3 | 0 | open_page -> extract_text -> verify_job -> finish | llm -> llm -> llm -> policy |
| qwen | search-happy-path | completed | target_reached | yes | 4 | 4 | 0 | search_jobs -> open_page -> extract_text -> verify_job -> finish | llm -> llm -> llm -> llm -> policy |
| qwen | open-recovery | completed | target_reached | yes | 3 | 3 | 0 | open_page -> extract_text -> verify_job -> finish | llm -> llm -> llm -> policy |
| qwen | verifier-recovery | completed | target_reached | yes | 5 | 5 | 0 | open_page -> extract_text -> open_page -> extract_text -> verify_job -> finish | llm -> llm -> llm -> llm -> llm -> policy |
| qwen | budget-exhaustion | partial | budget_exhausted | no | 1 | 1 | 0 | open_page -> finish | llm -> policy |

## 指标口径

- 任务完成率：达到 `target_reached` 的场景比例。
- 循环终止率：进入非 `running` 终态的场景比例，不能替代任务完成率。
- Fallback 率与非法决策率：分母均为 Hybrid runtime 实际调用 Planner 的次数。
- Token：只记录 provider 返回的 prompt/completion/total 数字，不保存 prompt 或响应正文；不硬编码货币价格。

## 面试表达

我把 deterministic policy、DeepSeek 和 Qwen 放进同一个五场景 Hybrid Agent runtime。
模型只负责正常状态下的语义选择，URL 白名单、失败恢复、重试预算和终止仍由代码控制。
因此我能分别报告任务完成、循环终止、非法决策、fallback、延迟与 Token，而不是只展示一次成功调用。
