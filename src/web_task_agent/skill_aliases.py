"""技能别名归一化（阶段 3 匹配层中文化）。

中文 JD 与英文简历（或反过来）之间隔着大量同义表述：
"大模型" ≈ "LLM" ≈ "大语言模型"，"检索增强" ≈ "RAG"，"爬虫" ≈ "spider"。
规则匹配做集合交集前先归一化，否则中文 skills 全部 miss（阶段 2 实测 0 分）。

设计：
- `SKILL_ALIAS_GROUPS`：别名组，每组一个 canonical 名（英文小写，便于程序处理）
- `normalize_skill()`：NFKC（全角→半角）+ casefold + strip + 别名→canonical
- `skill_variants()`：全部别名变体（供简历文本扫描——简历出现任一变体即视为
  具备该 canonical 技能）
- 未收录的技能：返回归一化后的原词（大小写/全半角仍对齐），行为可预测
"""

from __future__ import annotations

import unicodedata

# 每组第一个元素是 canonical 名
SKILL_ALIAS_GROUPS: list[tuple[str, ...]] = [
    ("llm", "大模型", "大语言模型", "large language model", "语言模型"),
    ("rag", "检索增强", "检索增强生成", "retrieval-augmented generation", "检索增强生成（rag）"),
    ("agent", "智能体", "ai agent", "多智能体", "agent 开发", "agentic"),
    ("nlp", "自然语言处理", "文本处理"),
    ("cv", "计算机视觉", "机器视觉", "视觉算法"),
    ("多模态", "multimodal", "多模态大模型", "multimodal大模型"),
    ("aigc", "生成式ai", "生成式人工智能", "generative ai"),
    ("爬虫", "spider", "crawler", "网络爬虫"),
    ("pytorch", "torch", "深度学习框架"),
    ("tensorflow", "tf"),
    ("机器学习", "machine learning", "ml"),
    ("深度学习", "deep learning", "dl"),
    ("数据挖掘", "data mining"),
    ("数据分析", "data analysis"),
    ("大模型训练", "预训练", "pretrain", "pretraining", "后训练", "sft", "强化学习训练"),
    ("langchain", "langgraph", "llm框架"),
    ("向量数据库", "vector database", "向量检索", "embedding检索"),
    ("提示词工程", "prompt engineering", "prompt工程", "prompt 工程"),
]

_ALIAS_TO_CANONICAL: dict[str, str] = {
    alias: group[0]
    for group in SKILL_ALIAS_GROUPS
    for alias in group
}


def normalize_skill(skill: str) -> str:
    """归一化单个技能名：全角→半角、casefold、去空白、别名→canonical。"""
    cleaned = unicodedata.normalize("NFKC", str(skill))
    cleaned = " ".join(cleaned.split()).casefold().strip()
    return _ALIAS_TO_CANONICAL.get(cleaned, cleaned)


def skill_variants() -> list[str]:
    """所有别名变体（canonical 在内），供简历文本子串扫描。"""
    return [alias for group in SKILL_ALIAS_GROUPS for alias in group]
