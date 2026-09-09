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


# ── 双维度分类(梯队 × 岗位类型,2026-09-08 用户需求)────────────────

# 岗位类型判定词:按优先级顺序匹配(算法 > 工程 > 产品 > 运营 > 设计)
CATEGORY_KEYWORDS = (
    # 长复合词优先(避免"产品运营"判成产品、"算法工程化"判成算法)
    ("运营", ("产品运营", "运营", "市场", "销售", "商业化", "增长")),
    ("产品", ("产品经理", "产品", "策划")),
    ("算法", ("算法工程师", "算法研究员", "算法", "研究", "Scientist", "scientist")),
    ("工程", ("工程师", "工程化", "工程", "开发", "研发", "架构")),
    ("设计", ("设计师", "设计", "UX")),
)


def classify_job_category(title: str) -> str:
    """按标题判定岗位类型:算法/工程/产品/运营/设计/其他。"""
    for category, words in CATEGORY_KEYWORDS:
        if any(word in title for word in words):
            return category
    return "其他"


# 公司梯队名单:精确匹配(官方列表源另有 _SPEC_TIER 静态表,两者互补)
TIER_COMPANIES = {
    "大厂": ("腾讯", "美团", "百度", "京东", "阿里巴巴", "阿里", "华为", "小米", "网易", "拼多多", "携程", "快手", "字节跳动", "字节"),
    "车企": ("蔚来", "理想", "小鹏", "比亚迪", "吉利", "长城"),
    "具身智能": ("宇树", "智元", "银河通用", "傅利叶", "优必选", "星动纪元", "众擎", "星海图", "逐际动力", "自变量", "智平方", "乐聚"),
    "AI 中厂": ("月之暗面", "智谱", "MiniMax", "minimax", "阶跃", "DeepSeek", "深度求索", "百川", "零一万物", "面壁", "生数", "爱诗", "Kimi"),
}


def classify_company_tier(company: str) -> str:
    """按公司名判定梯队:大厂/车企/具身智能/AI 中厂/中小厂(长尾)。"""
    for tier, names in TIER_COMPANIES.items():
        if any(name in company for name in names):
            return tier
    return "中小厂/长尾"
