"""共享的岗位方向关键词（发现过滤与验证过滤保持同一口径）。

阶段 2 中文化（2026-08-31）：verifier 原默认关键词 ["AI","LLM","Agent"] 全英文，
LLM 抽取完全正确的中文岗位被 `not relevant` 误杀（见 work-log
2026-08-31-aggregator-connector.md 第四节）。双语词表以中文 JD 的实际
用词为准，中英文岗位双覆盖。
"""

from __future__ import annotations

# AI 方向判定词（大小写不敏感匹配由调用方 lower() 后进行，中文不受影响）
AI_JOB_KEYWORDS = (
    "AI",
    "算法",
    "大模型",
    "大语言模型",
    "LLM",
    "机器学习",
    "深度学习",
    "NLP",
    "多模态",
    "Agent",
    "数据挖掘",
    "推荐",
    "CV",
    "视觉",
    "RAG",
)

# 实习判定词（仅用于发现侧标题过滤）
INTERN_TITLE_KEYWORDS = ("实习",)
