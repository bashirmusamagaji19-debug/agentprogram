# Work Log — 2026-08-31 阶段 1：聚合源 connector + 页面缓存

> 分支：`feature/chinese-job-pipeline` · 延续 `2026-08-31-chinese-jd-smoke.md`（阶段 0）

## 一、聚合源调研（4 个候选，实测后向用户确认）

| 仓库 | 活跃度 | 数据形态 | 结论 |
|---|---|---|---|
| Jasmine-Liu-min/job-radar | 今日更新 | data/jobs.json：3704 条，含 official_url/jd_text/公司/地点，193 条实习+AI | ✅ 选定 |
| wudiwen124/recruit | 今日更新 | 自托管 Node 服务（node≥24），爬 6 大厂官方 API | 备选：需维护外部服务 |
| luweitao31-lgtm/campus-jobs-2027 | 今日更新 | data/jobs.json 仅 6 条，0 条 AI 技术岗 | 格式好但数据太少 |
| amusi/AI-Job-Notes | 6148 star 但 2025-06 停更 | 攻略文档，无结构化岗位数据 | 排除 |

**用户确认**：job-radar 混合方案（解析 jobs.json 出 URL + 官方 API 直取正文兜底）。

## 二、关键实测发现

1. **官方详情页 5/5 全是 JS 渲染**（美团/腾讯/网易/国聘 HttpPageLoader 只拿到 2~11 字符的壳）——聚合源给的 URL 直接抓没用
2. **大厂官方招聘 API 是公开的、无鉴权、返回完整 JD**：
   - 腾讯 `GET /tencentcareer/api/post/ByPostId?postId=…`（详情页 URL 直接带 postId）
   - 美团 `POST /api/official/job/getJobList`（列表自带 jobDuty/jobRequirement，实习岗集中在第 4-7 页）
3. **腾讯已下线岗位的诚实信号**：HTTP 500 + `{"Code":500,"Data":"E1005"}`——归类为 `OfficialApiUnavailableError` 而非服务器错误
4. **ByPostId 的 E1005 排查过程**：先后怀疑 timestamp/Referer/UA/postId 大小写，最后用 Query 现役岗位交叉验证才定位到"岗位下线"语义

## 三、实现（4 个 commit）

- `07c0439` **AggregatorRepoSource**（`job_sources.py`）：JobSource Protocol + DiscoveredJob；解析 dict/裸数组两种 payload；实习+AI 标题过滤 + URL 去重；6 测试
- `208eca1` **page_cache 表 + CachedPageLoader**：24h TTL，命中不发 HTTP；6 测试（fake loader 计数）
- `818ee63` **OfficialApiContentFetcher**（`official_api.py`）：腾讯/美团域名路由；E1005→岗位不可用、超时→PageTimeoutError、其他→PageHttpError；8 测试 + 真实冒烟 4/4 符合预期
- `17f96e8` **CLI `--from-aggregator` + AggregatorPageLoader**：内容解析链 缓存→官方API→HttpPageLoader(≥50字才算有效)→jd_text 兜底，resolution_log 记录每 URL 实际策略；7 测试
- **fix（含在 17f96e8）**：`load_dotenv(override=True)` —— 排查出 CLI 内 .env 被 OS 残留旧 DASHSCOPE_API_KEY（41 字符）静默覆盖（新 key 35 字符），导致 LLM 抽取 401 退化为规则抽取。这是阶段 0 story #13 的根治

## 四、端到端验证（DASHSCOPE qwen × 3 次调用）

```
--from-aggregator jobs.json --aggregator-limit 3 --llm-extractor-provider qwen
→ Aggregator: discovered 3 intern+AI jobs
→ browser: opened 3 pages; failed 0（2 个 official-api，1 个 jd_text 兜底）
→ extractor: extracted 3 candidates（LLM 抽取，confidence 1.0，字段全对）
→ verifier: kept 1; filtered 2（not relevant —— 英文关键词过滤中文 JD，阶段 2 已知问题）
→ matcher: scored 1
```

**skills 字段的垃圾现状也被证实**：LLM 无 skills 字段时规则切分产出"1、2026 届获得本科及以上学历"整段——阶段 2 的 `skills: string[]` schema 扩展必要性再次实锤。

## 五、阶段 2 输入清单（已验证的具体问题）

1. verifier 默认关键词 `["AI","LLM","Agent"]` 过滤掉正确抽取的中文岗位（本阶段 2/3 被拦）
2. LLM schema 无 skills 字段，规则切分在中文整段上产出垃圾
3. `_LABELS`/section marker 无中文（规则抽取 requirements/responsibilities/location 0/6）
4. verifier 误杀与抽取质量可解耦测量（阶段 0 冒烟脚本 `required_keywords=[]` 诊断模式）
