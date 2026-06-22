# 真实站点 URL 验证与修正

## 背景

`build_real_site_sample_tasks()` 中有 4 个 URL，被标注为"真实站点样本"。用户（项目负责人）质询这些 URL 是否真的是真实站点，要求逐个验证。

## 验证过程

用 curl 逐个检查 4 个 URL：

```powershell
curl -s -o /dev/null -w "HTTP %{http_code}, size: %{size_download}b" --max-time 15 <url>
```

### 结果

| URL | HTTP 状态 | 大小 | 结论 |
|---|---|---|---|
| `openai.com/careers/ai-deployment-engineer-codex-remote-us/` | 000 (超时) | 0 | ❌ 假 URL |
| `openai.com/careers/ai-systems-engineer-codex-agents-san-francisco/` | 000 (超时) | 0 | ❌ 假 URL |
| `job-boards.greenhouse.io/anthropic/jobs/5116927008` | 200 | 100KB | ✅ 真实 Anthropic 岗位 |
| `job-boards.greenhouse.io/anthropic/jobs/5256303008` | 200 | 94KB | ✅ 真实 Anthropic 岗位 |

2 个 OpenAI URL 均超时（HTTP 000，15 秒无响应），确认 **不存在**。这些 URL 是 Codex 在项目初期生成 `build_real_site_sample_tasks()` 时编造的，格式模仿了 OpenAI 招聘 URL 但从未被验证。

2 个 Anthropic/Greenhouse URL 返回 HTTP 200，页面内容完整（分别约 100KB 和 94KB），包含真实岗位描述（"Applied AI Claude Evangelist, Startups" 和 "Technical Program Manager, API Platform"）。

## 修正

- 删除 2 个假 OpenAI URL
- 保留 2 个真实 Anthropic URL
- 更新 `build_real_site_sample_tasks()` 从 4 个 URL 减少到 2 个
- 更新测试 `test_build_real_site_sample_tasks_uses_live_job_seed_urls`：`assert len(tasks) == 2`
- 重新运行 `--compare-llm-extractor --real-site-sample --evaluation-count 2 --llm-extractor-provider deepseek`

## 修正后的评测结果

| Extractor | 完成 | 成功率 |
|---|---|---|
| baseline（规则） | 1/2 | 0.50 |
| llm-demo | 1/2 | 0.50 |
| deepseek | **2/2** | **1.00** |

DeepSeek 在真实 Anthropic 招聘页上 100% 完成，规则和 demo 各完成 1/2。

## 教训

1. **永远验证"真实数据"**：项目中的 seed URL、fixture URL、评测 URL 都需要用 curl 或等价方式验证可访问性，不能信任 Codex/AI 生成的 URL。
2. **URL 格式不等于可访问**：OpenAI 的 URL 格式看起来合理（`/careers/ai-deployment-engineer-codex-remote-us/`），但实际不存在。格式逼真不等于真实。
3. **评测数据要和评测结果一起审计**：之前的 `project-story.md` 写了"4 个真实招聘 URL"但只验证了 2 个。今后每次改评测数据，都要附带可验证的 HTTP 状态证明。

## 下一步

- 寻找更多可验证的招聘 URL 扩展真实样本库
- 考虑用 Greenhouse/Lever 等公开的招聘 API 或已知活跃岗位 ID
- 对每个加入的 URL 执行 curl 验证并记录 HTTP 状态和响应体大小
