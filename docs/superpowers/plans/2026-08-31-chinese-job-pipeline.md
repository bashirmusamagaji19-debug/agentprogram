# 中文岗位真实可用管线 — 实施计划

> 日期：2026-08-31 · 分支：`feature/chinese-job-pipeline`
> 目标：让 web-task-agent 从"英文 fixture 演示"变为"真实可用的中文岗位匹配工具"
> 数据来源：GitHub 实习信息聚合仓库（发现）+ 本地低频浏览器（JS 渲染页兜底）

## 验收标准（Definition of Real-Usable）

1. 从聚合源一次拉取 ≥10 个真实中文岗位，走完 抽取→验证→匹配→报告 全链路
2. 中文 JD 抽取有诚实数字：规则 vs LLM 的字段成功率对比（新增 `skills` 结构化字段后）
3. 用真实简历跑一次，人工确认 Top-5 匹配中 ≥3 个值得投
4. 每阶段的问题与修复都追加到 `docs/interview-debugging-stories.md` 和 work-log

## 现状关键事实（已核实）

- `llm_extractor.py` 的抽取 schema **无 `skills` 字段**；`extractor.py:214` 按逗号切 requirements 得到 skills——中文 JD 的 requirements 是整段话，切分结果是垃圾
- `matcher.py` 的 `_known_skill_terms()` 全英文；规则匹配对中文输入基本失效（语义匹配 prompt 已支持中文输出，是现成的兜底）
- `storage.py` 只有 jobs/run_metrics/save_receipts 三张表，**无页面缓存**
- `HttpPageLoader` 对 JS 渲染页会抛 `PageEmptyError`（失败分类体系现成）；`BrowserUseClient` 是真实的 browser-use session adapter
- `.env` 已存在，DEEPSEEK/DASHSCOPE key 大概率已配置（执行时确认）
- 项目惯例：feature branch + 小步 commit（feat:/fix:/docs:）、work-log 按日期、评测证据进 `docs/results/`

## 阶段 0：中文 JD 冒烟测试（半天）— 探路，决定后续细节

1. 收集 6~10 个真实中文 JD URL：实习僧 2-3 个、牛客 2-3 个、大厂官网 careers 2 个（执行时用 web 搜索获取有效 URL；如用户有收藏的更好）
2. 逐个用 `HttpPageLoader` 直接抓取（简单脚本），记录：哪些站返回空正文（`empty_page` → 需浏览器）、哪些能拿到正文
3. 能拿到正文的页面跑现有 `--seed-url ... --llm-extractor-provider deepseek` 路径，观察中文抽取表现
4. **产出**：`docs/work-log/2026-08-31-chinese-jd-smoke.md`（按站点记录可访问性矩阵）+ debugging stories 追加
5. 此阶段不改任何生产代码，纯测量

## 阶段 1：聚合源 connector + 页面缓存（1-2 天）

**新增 `src/web_task_agent/job_sources.py`**：
- `JobSource` Protocol：`async def discover(self, limit: int) -> list[DiscoveredJob]`（含 url/title/company 等可选元数据）
- `AggregatorRepoSource`：读取聚合仓库的 markdown/JSON（先 `git pull` 或直接拉 raw 文件），解析出岗位条目 → 转为 seed URLs
- 执行时先调研 2-3 个候选聚合仓库（GitHub 每日实习信息类），**选定后向用户确认**再写解析器——不同仓库格式差异大，解析器按实际格式写

**`storage.py` 加 `page_cache` 表**：`(url PRIMARY KEY, content, title, source, fetched_at)`；`get_cached_page(url)` / `cache_page(page)` + TTL 检查（默认 24h）。`HttpPageLoader` 可选包一层 `CachedPageLoader`

**CLI**：`--from-aggregator <repo-path-or-url>` 与现有 `--seed-url` 合流进 `candidate_urls`

**测试**：fixture 聚合文件（脱敏样本）→ 解析出的 URL 列表断言；缓存命中不发 HTTP（fake loader 计数）

## 阶段 2：抽取链中文化（1-2 天）

1. **`llm_extractor.py`**：抽取 prompt 双语化，schema 增加 `skills: string[]`（要求输出规范化技能名，中英皆可，如 `["Python", "大模型", "LangChain"]`）；`__call__` 返回值加 skills
2. **`extractor.py`**：
   - `_LABELS` 增加中文标签（职位名称/岗位/公司/工作地点/任职要求/岗位职责/发布时间）
   - skills 来源改为：LLM skills 字段优先，无 LLM 时保留现有切分兜底
   - `_infer_public_job_fields` 的英文 section marker 增加中文对应（岗位职责/任职要求/职位描述）
3. **`verifier.py`**：确认关键词相关性过滤不会误杀中文岗位（检查其关键词表，可能需要加中文关键词）
4. **测试**：新增中文 JD fixture 页面（构造 2-3 个：标签式、无标签整段式、实习僧风格），fake transport 测试抽取与 skills 输出

## 阶段 3：匹配层中文化（1-2 天）

1. **`matcher.py`**：
   - 新增技能别名归一化（新文件 `skill_aliases.py`：`LLM↔大模型↔大语言模型`、`爬虫↔spider`、`AIGC`、`RAG↔检索增强` 等映射表，大小写/全半角归一）
   - `_known_skill_terms()` 扩充中文技能词
   - 规则匹配前先做别名归一化，再交集
2. **验证 LLM 语义匹配在中文输入下的表现**（prompt 已中文友好，实测调整）
3. **小标注集**：`evaluations/ground-truth/matching.jsonl`——10~20 条 (岗位 skills, 用户画像, 人工标注的匹配/不匹配)，新增 `--evaluate-matcher` 输出规则 vs LLM 的准确率对比
4. **测试**：别名归一化单测 + 中文匹配 fixture

## 阶段 4：低频浏览器路径（1 天，按需）

1. 阶段 0 中判定为"必须浏览器"的站点，用 `BrowserUseClient` 实测（本地、headless）
2. 加最小限速：页面间随机等待 3~8s（新参数 `--politeness-delay`，默认开）
3. 页面缓存接入：命中缓存不重抓
4. 不做任何验证码处理/指纹伪装——失败即分类记录（`browser_error` 等），这是明确边界
5. 若实测发现某站点完全不可行，记录结论并从站点列表移除——诚实记录比硬上重要

## 阶段 5：自用端到端实验 + 收尾（半天）

1. 用户真实简历 + 聚合源 ≥10 岗位，跑 `--from-aggregator ... --llm-extractor-provider deepseek --llm-match-provider deepseek --dashboard --action-plan`
2. 人工复核 Top-5，记录命中率 → `docs/work-log/2026-09-XX-real-chinese-run.md`
3. 更新 README（新增"中文岗位真实使用"章节，沿用项目诚实边界的写法：标注样本量、不夸大泛化）
4. 汇总各阶段 debugging stories，补"面试反问预案"新条目
5. `--release-check` + 全量 pytest 通过后收尾

## 执行纪律

- 在 `Agent` 仓库开 `feature/chinese-job-pipeline` 分支，沿用项目 commit 风格（`feat:` / `fix:` / `docs:`，一次一事）
- 每个阶段的调试问题**当场**追加进 `docs/interview-debugging-stories.md`（问题→排查→解决→一句话面试回答）
- 涉及真实 API 调用的验证命令先在对话里说明再跑（会消耗用户的 API 配额）
- 聚合仓库选定、阶段 5 的简历内容，需要用户输入/确认的当场问，不自行假设

## 风险与已知不确定点

| 风险 | 应对 |
|------|------|
| 实习僧等站点是 SPA，HttpPageLoader 全挂 | 阶段 0 提前测量；必要时全走阶段 4 浏览器路径 |
| 聚合仓库格式不稳定/停更 | 阶段 1 调研 2-3 个候选做备份源；`JobSource` Protocol 保证可替换 |
| 中文 JD 的 LLM 抽取质量未知 | 阶段 0 冒烟先测；schema 加 skills 后若仍差，调 prompt（低风险迭代） |
| browser-use 在国内站点实际表现未知 | 阶段 4 单独验证；失败分类体系会给出诚实答案 |

## 总工作量估计

约 5~7 个工作日（阶段 0+1+2+3 为核心路径约 4 天，阶段 4 视阶段 0 结果可裁剪，阶段 5 半天）
