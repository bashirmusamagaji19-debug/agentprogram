"""skill_aliases 与中文匹配测试（阶段 3）。"""

from __future__ import annotations

import pytest

from web_task_agent.matcher import JobMatcher
from web_task_agent.models import JobPosting, UserProfile
from web_task_agent.skill_aliases import normalize_skill, skill_variants


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
