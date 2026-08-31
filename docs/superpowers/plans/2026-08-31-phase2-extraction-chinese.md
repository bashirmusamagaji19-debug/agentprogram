# 阶段 2：抽取链中文化 — 实施计划

> 日期：2026-08-31 · 分支：`feature/chinese-job-pipeline`
> 上游：`2026-08-31-chinese-job-pipeline.md` 阶段 2（阶段 0/1 已完成，实测输入见下）
> 预计工作量：1~1.5 天

## 阶段 0/1 实测输入（本阶段的靶子，全部已验证）

1. **verifier 误杀（最高优先级）**：默认关键词 `["AI","LLM","Agent"]` 全英文，LLM 抽取完全正确的中文岗位 2/3 被 `not relevant` 拦截（阶段 1 端到端冒烟实测）
2. **LLM schema 缺 skills**：抽取出的"技能"是规则逗号切分的整段句子（"1、2026 届获得本科及以上学历"），进 matcher 就是垃圾
3. **规则抽取中文基线 0/6**：`_LABELS` 无中文标签；section marker（`requirements`/`qualifications`）匹配不到中文；`_looks_like_location` 不认中文城市
4. **全角冒号未处理**（代码审读发现）：`_parse_labeled_lines` 用 `partition(":")` 半角冒号，中文 JD 标签行几乎全是全角"："——不加支持则中文标签解析整条失效
5. verifier 误杀与抽取质量可解耦测量：`required_keywords=[]` 诊断模式已在冒烟脚本验证

## 验收标准

1. 阶段 1 冒烟中被拦的 2 个中文岗位（商业分析实习生-大模型应用BP、大模型算法工程师-实习生）可通过 verifier
2. LLM 抽取在真实中文 JD 上输出 `skills: string[]`，内容是规范化技能名（如 `["Python", "大模型", "RAG"]`），不是整段句子
3. 规则抽取在 6 个牛客中文 JD 上 requirements/responsibilities/location 成功率从 0/6 显著提升（`baseline_rule_extraction.py` 前后对比数字如实记录）
4. 全量 pytest 通过，英文 fixture 路径零回归

## 任务分解（按依赖顺序，一次一事的 commit）

### 任务 1：verifier 中文化（fix，~0.5h）

- 新增模块级常量 `AI_JOB_KEYWORDS`（中英双语）：`AI/LLM/Agent + 人工智能/大模型/算法/机器学习/深度学习/NLP/多模态/数据挖掘/推荐/视觉/RAG`（与 `job_sources.py` 的 `AI_TITLE_KEYWORDS` 口径对齐，可从 job_sources 导入复用）
- `verifier.py`：`__init__` 默认值改为该常量；大小写归一仅对英文生效（中文不受 lower 影响，现有逻辑天然兼容）
- `cli.py` 两处硬编码同步：
  - `build_workflow` 的 `JobVerifier(required_keywords=["AI","LLM","Agent"])` → 用常量
  - `run_llm_matcher_comparison` 的英文关键词表（cli.py:1479 附近）→ 用常量
- **测试**：中文标题/正文岗位 verify 通过；英文岗位不回归；空关键词诊断模式不回归
- commit：`fix: bilingual AI keyword filter stops killing valid Chinese jobs`

### 任务 2：LLM 抽取 schema 加 skills（feat，~2h）

- `llm_extractor.py`：
  - `_payload` prompt 双语化：系统提示声明"JD 可能是中文或英文"；用户指令字段列表增加 `skills`，明确口径：**从 requirements/responsibilities 提取具体技术栈/工具/领域，输出规范化短名（中英皆可），禁止输出整段句子或编号条目**，示例 `["Python", "大模型", "LangChain", "RAG"]`
  - `__call__` 返回值加 `"skills"`：list 处理（每项 str.strip、去空、去重、保序），非 list 忽略
  - 注意与 `_string_or_join` 区分：skills 必须保持 list，不能 join 成句子
- `extractor.py`：
  - `extract` 的 LLM 分支与 `_job_from_fields`：`skills = llm_fields.get("skills") or 规则切分兜底`
  - `JobPosting.skills` 已是 `list[str]`，无需改模型
- **测试**（fake transport）：返回带 skills 的 JSON → JobPosting.skills 正确；无 skills 键 → 回退规则切分；skills 含非字符串项 → 过滤
- 验证：qwen 真实调用 2~3 次（先说明再跑），检查 skills 输出质量；若出现整段句子，迭代 prompt
- commit：`feat: llm extractor emits normalized skills array for Chinese JDs`

### 任务 3：规则抽取中文化（feat，~2-3h）

- `extractor.py`：
  - `_LABELS` 加中文标签：职位名称/岗位→title；公司/公司名称→company；工作地点/地点/城市→location；任职要求/岗位要求/任职资格→requirements；岗位职责/工作职责/职责→responsibilities；发布时间/发布日期→posted_at
  - `_parse_labeled_lines` 支持**全角冒号**：partition 前先把"："归一为":"（或先按"："partition）；同时处理标签后直接跟内容但冒号缺失的行？——**不做**，只处理有冒号的行，保持行为可预测，诚实记录边界
  - `_infer_public_job_fields` marker 加中文：responsibilities start `岗位职责/工作职责/工作内容/职位描述`；requirements start `任职要求/岗位要求/任职资格`；stop 集合交叉补齐中文
  - `_matches_marker` 对中文 section 行（"任职要求："）兼容：marker 匹配前剥离尾部冒号
  - `_looks_like_location` 加中文城市 token：北京/上海/深圳/杭州/广州/成都/南京/武汉/西安/远程/全国
- **测试**：新增 `tests/fixtures/chinese_job_pages.py`（2 个页面：牛客风格"标签+全角冒号"式、无标签整段式），规则抽取逐字段断言
- commit：`feat: rule extraction supports Chinese labels, sections and locations`

### 任务 4：基线复测 + 诚实数字（docs，~1h）

- 重跑 `scripts/baseline_rule_extraction.py`（同 6 个牛客 URL），与阶段 0 的 0/6 基线对比，数字进 work-log
- 重跑阶段 1 的端到端冒烟命令（`--from-aggregator --aggregator-limit 3 --llm-extractor-provider qwen`），预期 3/3 通过 verifier（qwen 调用 ≤3 次，先说明再跑）
- 追加 debugging story：全角冒号坑（中文标签解析整条失效的隐性 bug）
- commit：`docs: phase 2 before/after numbers and full-width colon story`

## 明确不做（边界）

- **matcher 中文化**（`_known_skill_terms`、别名归一化）是阶段 3，本阶段 skills 进规则匹配仍可能 0 分——不越界，中文语义匹配已有 LLM 兜底
- **GLM provider 接入**（Anthropic 兼容 /v1/messages，与现有 OpenAI 兼容 transport 不同）：DASHSCOPE 当前可用，暂不实现；若执行中 DASHSCOPE 再次 401，作为应急插入（用户的备胎授权）
- 标签变体不穷尽（如"【任职要求】"方括号式）：fixture 只覆盖主流两种，其余诚实记录为已知边界

## 风险

| 风险 | 应对 |
|------|------|
| LLM 输出 skills 质量不稳定（整段句子混入） | prompt 明确"短名+示例"，temperature=0；实测不合格则迭代 prompt（低风险循环） |
| 中文 marker 匹配误触发（正文里出现"任职要求"字样被当 section 起点） | marker 匹配限定行首且行短（≤20 字），fixture 用真实牛客页面校验 |
| verifier 放宽后混入不相关岗位 | 关键词口径与 job_sources 的发现过滤一致（标题已含 AI 关键词才进来），双保险而非放宽 |

## 执行纪律

- 沿用 feature branch + 小步 commit（fix:/feat:/docs:，一次一事）
- 调试问题当场追加 `docs/interview-debugging-stories.md`
- 真实 API 调用先说明再跑（qwen ≤6 次：任务 2 验证 ≤3 + 任务 4 复测 ≤3）
- 完成后回到主计划推进阶段 3（匹配层中文化）
