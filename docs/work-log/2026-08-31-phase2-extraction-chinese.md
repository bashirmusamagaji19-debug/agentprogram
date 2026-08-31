# Work Log — 2026-08-31 阶段 2：抽取链中文化

> 分支：`feature/chinese-job-pipeline` · 计划：`docs/superpowers/plans/2026-08-31-phase2-extraction-chinese.md`

## 一、改动总览（4 个 commit）

| commit | 内容 |
|---|---|
| `1b9ecfd` | fix: verifier 双语关键词（keywords.py 共享常量，发现/验证同一口径） |
| `2f46ec6` | feat: LLM 抽取 schema 加 `skills: string[]`（prompt 双语化 + 短名口径） |
| （本次） | feat: 规则抽取中文化（全角冒号/中文标签/section marker/中文城市） |
| （本次） | docs: 本 work-log + debugging stories |

## 二、诚实数字（6 个牛客真实中文 JD，脚本 `scripts/baseline_rule_extraction.py`）

| 字段 | 阶段 0 基线 | 阶段 2 后 | 说明 |
|---|---|---|---|
| title | 6/6 | 6/6 | 页面 title 兜底 |
| company | 6/6 | 5/6 | **变化是改进**：此前"首页"等导航垃圾计入 OK；现在阿里页标签分支命中后诚实输出 Unknown（触发 LLM 兜底） |
| location | 0/6 | 0/6 | **页面噪音问题**：牛客正文混有"首页/题库"导航行，地点不在头部行结构里——规则层无解，靠 LLM 兜底 |
| requirements | 0/6 | **5/6** | 中文 section marker（任职要求/任职资格）命中 |
| responsibilities | 0/6 | **5/6** | 中文 section marker（岗位职责/工作职责）命中 |
| confidence | 0.40 | 0.80 | 5/6 页面不再必然触发 LLM（0.6 阈值） |

## 三、版式发现（规则层能解决 vs 不能解决的）

**能解决（本次已修）**：
1. **全角冒号**："任职要求：" 用 `partition(":")` 半角解析整条失效 → 先半角后全角的两段式切分
2. **section 标题行版式**：中文 JD 的"岗位职责："是**值为空的标题行**，内容在后续行（区别于英文 "Requirements: xxx" 同行式）→ 标签解析支持收集后续行直到下一个标签行
3. **"上海市·浦东新区"整行是地点**：分隔符切分后 company 部分也像地点 → 公司名回退取上一行
4. **中文城市识别**：`_looks_like_location` 加 16 个中文城市/地点 token

**不能解决（如实记录边界）**：
- 牛客页面正文混有导航噪音（"首页/题库/公司真题/…"），location 与头部 company 的推断被污染——这是**页面级清洗**问题，超出抽取规则层；LLM 兜底（现有机制）在这些字段上继续发挥作用

## 四、端到端复测（qwen × 3 次调用，与阶段 1 完全相同的命令）

- 阶段 1：3 岗位 → verifier 拦 2 → **1 valid**，skills 全为整段句子垃圾
- 阶段 2：3 岗位 → **3 valid（0 误杀）**，skills 为规范化短名（如 `["NLP", "TensorFlow", "PyTorch", "语义理解", "对话系统", "问答系统"]`）
- 已知边界：jd_text 兜底岗位若正文缺 requirements 段，LLM skills 输出为空（诚实行为，不造假）
- matcher 全 0 分：预期——规则匹配中文 skills 是阶段 3 的任务，LLM 语义匹配未开启

## 五、面试要点（已追加 stories #16）

中文 JD 的"标签：值"和英文不同：标签行值常为空、内容在后续行（section 式）。只做"全角冒号替换"这类表面适配会漏掉这个结构性差异——先看真实页面的版式再写解析器。
