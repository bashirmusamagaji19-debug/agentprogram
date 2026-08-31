# Work Log — 2026-08-31 阶段 3：匹配层中文化

> 分支：`feature/chinese-job-pipeline` · 上游：阶段 2（`2026-08-31-phase2-extraction-chinese.md`）

## 一、改动总览

| commit | 内容 |
|---|---|
| `8eaadac` | feat: skill_aliases.py（18 组别名，NFKC+casefold+canonical）+ matcher 归一化交集 |
| （本次） | feat: 标注集 `evaluations/ground-truth/matching.jsonl`（16 条）+ CLI `--evaluate-matcher` |
| （本次） | docs: 本 work-log + story #17 |

## 二、别名归一化设计

- `SKILL_ALIAS_GROUPS`：每组一个 canonical（英文小写），如 `大模型/大语言模型/Large Language Model → llm`
- `normalize_skill()`：NFKC（全角`Ｐｙｔｈｏｎ`→半角）+ casefold + 别名→canonical；未收录技能返回归一化原词（行为可预测）
- `matcher._rule_match`：**归一化后**再交集；`matched_skills` 显示保留岗位原文（用户体验）
- `_user_signal`：已知词表（扩到 28 个中英词）+ `skill_variants()` 双重扫描简历——简历写"检索增强"、岗位写 RAG 也能互相命中

## 三、标注集评测（16 条，`--evaluate-matcher`）

| 匹配器 | 准确率 | 错在哪 |
|---|---|---|
| 规则（归一化后） | **0.94 (15/16)** | 只错 #5 语义边界（"大模型/多智能体/提示词工程" vs 会 prompt 工程+LangChain，1/3=0.33 判不投） |
| LLM（qwen-plus） | **0.81 (13/16)** | 赢回 #5（0.75 判投），但在 3 个 no_match 上过度乐观：#14 测试岗 0.85、#12 商业分析 0.65、#8 AI Infra 0.45 |

**核心发现（面试素材）**：
1. 规则匹配在"明确不相关"判断上**比 LLM 稳**——LLM 看到"会 Python"就在无关岗位（测试/商业分析）上抬分（乐观偏差）
2. LLM 在"语义等价"上**比规则强**——#5 边界样本只有它判对
3. 这正是**分层设计**的经验证据：规则优先（快、稳、免费）+ LLM 兜底语义边界，而不是二选一

**诚实声明**：标注集由别名表设计而来（验证"归一化管线符合设计意图"），规则 0.94 存在自产自销偏差，不外推为泛化准确率；LLM 对比维度相对独立。

## 四、指标口径

- 判定阈值 `score >= 0.4`（与 priority medium 以上一致）：值得投递
- `--evaluate-matcher` 输出：规则/LLM 各自准确率 + 分歧条目列表；报告 `matcher-evaluation.md`
- 无 `--llm-match-provider` 时纯规则评测（零 API 成本），可日常回归

## 五、勘误（2026-08-31 当晚 bug 排查轮）

本表上半部分写完后做了一轮专门 bug 排查（stories #18–#20），修正三处：

| 问题 | 影响 | 修复 |
|---|---|---|
| ASCII 缩写裸子串匹配 | 简历含 HTML/YAML/XML 被记为会机器学习（"ml" 子串），ML 岗虚高 1.0 | `term_in_text()` ASCII 边界匹配（`8cf39f9`） |
| 官方 API 复合标签头 | 腾讯/美团页规则抽取全空，每岗强制 LLM 抽取 | 适配器输出标准标签行（`41b4fed`） |
| **评测口径污染** | **"LLM 0.81" 实为混合口径**——规则 ≥0.6 的 8/16 条没真调 LLM，llm_score 是规则分顶替 | 评测逐条直调 llm_matcher（`d5a6390`） |

纯 LLM 重测：**0.81 (13/16)**，与旧数字巧合一致（被顶替的 8 条恰好是 LLM 能做对的 match 样本），失败模式不变（#8/#12/#14 乐观偏差）。核心结论"规则 0.94 + LLM 兜底分层"成立，口径已可辩护。规则侧 0.94 在边界修复后复测不变。

## 六、遗留到阶段 5

- 真实简历 + ≥10 聚合岗位的端到端跑（阶段 5 验收）
- 潜在优化：规则判 low（<0.4）且 LLM 判 high 时的混合仲裁策略——本次分歧数据是其输入，暂不做
