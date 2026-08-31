"""skill_aliases 与中文匹配测试（阶段 3）。"""

from __future__ import annotations

import pytest

from web_task_agent.matcher import JobMatcher
from web_task_agent.models import JobPosting, UserProfile
from web_task_agent.skill_aliases import normalize_skill, skill_variants, term_in_text


# ── normalize_skill 基础行为 ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("LLM", "llm"),
        ("llm", "llm"),
        ("大模型", "llm"),
        ("大语言模型", "llm"),
        ("Large Language Model", "llm"),
        ("RAG", "rag"),
        ("检索增强", "rag"),
        ("检索增强生成", "rag"),
        ("爬虫", "爬虫"),
        ("Spider", "爬虫"),
        ("CRAWLER", "爬虫"),
        ("Agent", "agent"),
        ("智能体", "agent"),
        ("Ｐｙｔｈｏｎ", "python"),  # 全角字母
        ("  Python  ", "python"),
    ],
)
def test_normalize_skill_alias_groups(raw: str, expected: str):
    assert normalize_skill(raw) == expected


def test_normalize_skill_unlisted_keeps_normalized_original():
    assert normalize_skill("Docker") == "docker"
    assert normalize_skill("微服务") == "微服务"


def test_skill_variants_cover_all_groups():
    variants = skill_variants()
    assert "大模型" in variants and "llm" in variants
    assert "检索增强" in variants and "rag" in variants


# ── matcher 集成：中文 JD ↔ 英文简历 / 反向 ──────────────────────────


def make_job(skills: list[str], **overrides) -> JobPosting:
    data = {
        "title": "大模型算法实习生",
        "company": "示例公司",
        "location": "北京",
        "source": "fixture",
        "url": "https://example.com/cn-job",
        "requirements": "熟悉大模型与Python",
        "responsibilities": "参与大模型训练",
        "skills": skills,
        "confidence": 0.9,
    }
    data.update(overrides)
    return JobPosting(**data)


def test_rule_match_chinese_jd_english_resume_cross_alias():
    """岗位写"大模型/检索增强"，简历会 "LLM/RAG" —— 归一化后命中（阶段 2 前全 0）。"""
    matcher = JobMatcher()
    user = UserProfile(
        keyword="AI 实习",
        skills=["Python", "LLM", "RAG"],
        resume_text="用 LangGraph 构建过 Agent。",
    )
    job = make_job(skills=["Python", "大模型", "检索增强"])

    result = matcher.match(user=user, job=job)

    assert result.score == 1.0
    assert result.matched_skills == ["Python", "大模型", "检索增强"]  # 显示保留岗位原文
    assert result.missing_skills == []


def test_rule_match_reverse_direction_english_jd_chinese_resume():
    """反向：英文岗位 skills，中文简历——"检索增强"在简历里命中 RAG。"""
    matcher = JobMatcher()
    user = UserProfile(
        keyword="AI 实习",
        skills=[],
        resume_text="做过检索增强生成（RAG）系统和网络爬虫。",
    )
    job = make_job(skills=["RAG", "爬虫", "Docker"])

    result = matcher.match(user=user, job=job)

    # RAG 与 爬虫 通过简历别名命中；Docker 缺失（score round 到 2 位）
    assert result.score == 0.67
    assert "RAG" in result.matched_skills
    assert "爬虫" in result.matched_skills
    assert result.missing_skills == ["Docker"]


def test_rule_match_fullwidth_characters_in_skills():
    """全角字母技能（Ｐｙｔｈｏｎ）归一后命中半角简历。"""
    matcher = JobMatcher()
    user = UserProfile(keyword="AI 实习", skills=["Python"], resume_text="")
    job = make_job(skills=["Ｐｙｔｈｏｎ"])

    result = matcher.match(user=user, job=job)

    assert result.score == 1.0


def test_rule_match_unmatched_chinese_skills_still_reported():
    """真不匹配的技能照常进 missing（归一化不虚报命中）。"""
    matcher = JobMatcher()
    user = UserProfile(keyword="AI 实习", skills=["Python"], resume_text="")
    job = make_job(skills=["Python", "Java微服务", "Kubernetes"])

    result = matcher.match(user=user, job=job)

    assert result.score == 0.33
    assert result.missing_skills == ["Java微服务", "Kubernetes"]


# ── 短 ASCII 缩写的边界匹配（bug 排查实录 #18）──────────────────────


@pytest.mark.parametrize(
    ("term", "text", "expected"),
    [
        # HTML/YAML/XML 含 "ml" 子串，但不是机器学习
        ("ml", "熟悉 html/css 前端", False),
        ("ml", "配置过 yaml 和 xml", False),
        # 独立出现的缩写应命中（含紧邻中文的情况）
        ("ml", "熟悉 ml 建模", True),
        ("ml", "做过ML项目", True),
        ("dl", "了解 dl 框架", True),
        ("dl", "熟悉 handlebars 模板", False),
        # 中文词保持子串
        ("机器学习", "系统学习过机器学习课程", True),
        ("检索增强", "做过检索增强生成", True),
    ],
)
def test_term_in_text_ascii_boundary(term: str, text: str, expected: bool):
    assert term_in_text(term, text.casefold()) is expected


def test_frontend_resume_not_credited_with_machine_learning():
    """简历只写 HTML/YAML/XML（含 "ml" 子串）不得被记为会机器学习。"""
    matcher = JobMatcher()
    user = UserProfile(
        keyword="前端",
        skills=[],
        resume_text="熟悉 HTML/CSS 前端，配置过 YAML 和 XML。",
    )
    job = make_job(title="机器学习实习生", skills=["机器学习"])

    result = matcher.match(user=user, job=job)

    assert result.score == 0.0
    assert result.missing_skills == ["机器学习"]


# ── 编号长句式 skills 的文本扫描降级（bug 排查实录 #22）──────────────


def test_numbered_sentence_skills_fall_back_to_text_scan():
    """真实中文 JD 的编号长句 skills 不再全 0：CV 简历应命中 PyTorch/深度学习。"""
    matcher = JobMatcher()
    user = UserProfile(
        keyword="CV",
        skills=["PyTorch", "计算机视觉"],
        resume_text="基于 PyTorch 做目标检测，熟悉深度学习与 CUDA。",
    )
    # 模拟官方 API 抽取产物：逗号切分出的编号长句碎片
    job = make_job(
        title="大模型算法工程师（实习生）",
        skills=[
            "1、扎实的算法基础",
            " 熟悉NLP相关算法和模型；",
            " 2、有Tensorflow",
            " pytorch等深度学习框架与自然语言处理结合实际项目经验者优先 3、有语义理解、对话系统、问答系统等相关项目经验者优先。",
        ],
        requirements=(
            "1、扎实的算法基础，熟悉NLP相关算法和模型；"
            "2、有Tensorflow, pytorch等深度学习框架与自然语言处理结合实际项目经验者优先；"
            "3、有语义理解、对话系统、问答系统、机器翻译、知识图谱等相关项目经验者优先。"
        ),
    )

    result = matcher.match(user=user, job=job)

    assert result.score > 0  # 修复前必然 0.00（句子碎片与技能词无交集）
    assert "PyTorch" in result.matched_skills
    assert "深度学习" in result.matched_skills
    assert "自然语言处理" in result.missing_skills  # CV 简历没写 NLP（显示首个扫中变体）


def test_clean_skills_still_use_exact_match_path():
    """干净技能词列表走原精确交集路径，不被文本扫描降级影响。"""
    matcher = JobMatcher()
    user = UserProfile(keyword="AI", skills=["Python", "LLM"], resume_text="")
    job = make_job(skills=["Python", "大模型", "检索增强"])

    result = matcher.match(user=user, job=job)

    assert result.score == 0.67
    assert result.matched_skills == ["Python", "大模型"]
