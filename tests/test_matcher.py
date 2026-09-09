import pytest

from web_task_agent.matcher import JobMatcher
from web_task_agent.models import JobPosting, UserProfile


def make_job(**overrides):
    data = {
        "title": "AI Engineering Intern",
        "company": "Example AI",
        "location": "Remote",
        "source": "fixture",
        "url": "https://example.com/jobs/1",
        "requirements": "Python, LangGraph, LLM, FastAPI",
        "responsibilities": "Build AI agents",
        "skills": ["Python", "LangGraph", "LLM", "FastAPI"],
        "confidence": 0.9,
    }
    data.update(overrides)
    return JobPosting(**data)


def test_matcher_scores_job_from_user_skills():
    matcher = JobMatcher()
    user = UserProfile(
        keyword="AI intern",
        skills=["Python", "LangGraph"],
    )

    result = matcher.match(user=user, job=make_job())

    assert result.job_id == "https://example.com/jobs/1"
    assert result.score == 0.5
    assert result.matched_skills == ["Python", "LangGraph"]
    assert result.missing_skills == ["LLM", "FastAPI"]
    assert result.priority == "medium"
    assert "2/4" in result.reason


def test_matcher_uses_resume_text_as_skill_signal():
    matcher = JobMatcher()
    user = UserProfile(
        keyword="AI intern",
        skills=["Python"],
        resume_text="Built LangGraph agents with FastAPI services.",
    )

    result = matcher.match(user=user, job=make_job())

    assert result.score == 0.75
    assert result.matched_skills == ["Python", "LangGraph", "FastAPI"]
    assert result.missing_skills == ["LLM"]
    assert result.priority == "high"


def test_matcher_handles_job_without_skills():
    matcher = JobMatcher()
    user = UserProfile(keyword="AI intern", skills=["Python"])
    job = make_job(skills=[], requirements="")

    result = matcher.match(user=user, job=job)

    assert result.score == 0.0
    assert result.matched_skills == []
    assert result.missing_skills == []
    assert result.priority == "low"
    assert result.suggested_actions == ["补充岗位技能要求后再评估。"]


def test_matcher_matches_many_jobs_in_order():
    matcher = JobMatcher()
    user = UserProfile(keyword="AI intern", skills=["Python", "LLM"])
    jobs = [
        make_job(title="A", url="https://example.com/a", skills=["Python"]),
        make_job(title="B", url="https://example.com/b", skills=["FastAPI"]),
    ]

    results = matcher.match_many(user=user, jobs=jobs)

    assert [result.job_id for result in results] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert results[0].score == 1.0
    assert results[1].score == 0.0


# ─── LLM matcher integration tests ────────────────────────────────────


class _FakeLlmMatcher:
    """Fake LLM matcher for deterministic testing — does not hit a real API."""

    def __call__(self, payload: dict[str, str]) -> dict[str, object]:
        return {
            "score": 0.85,
            "matched_skills": ["Python", "FastAPI"],
            "missing_skills": ["SQL"],
            "reason": "语义分析：你的 Python 和 API 开发背景与岗位高度匹配。",
            "priority": "high",
            "suggested_actions": ["补强 SQL 项目经历。"],
        }


def test_matcher_falls_back_to_llm_when_rule_score_low():
    matcher = JobMatcher(llm_matcher=_FakeLlmMatcher())
    user = UserProfile(keyword="AI intern", skills=["Python"])
    job = make_job(skills=["FastAPI", "SQL"])
    # rule score = 0/2 = 0.0 < 0.6 → LLM fallback（分数折减 0.55，见 #24）

    result = matcher.match(user=user, job=job)

    assert result.score == round(0.85 * 0.55, 2)  # 0.47
    assert result.matched_skills == ["Python", "FastAPI"]
    assert result.missing_skills == ["SQL"]
    # priority 按折减后分数重算（0.47 → medium），不是 LLM 自报的 high（#24 实测）
    assert result.priority == "medium"
    assert "语义分析" in result.reason
    assert "折减" in result.reason


def test_matcher_skips_llm_when_rule_score_high():
    matcher = JobMatcher(llm_matcher=_FakeLlmMatcher())
    user = UserProfile(keyword="AI intern", skills=["Python", "LangGraph"])
    job = make_job(skills=["Python", "LangGraph"])
    # rule score = 2/2 = 1.0 >= 0.6 → no LLM fallback

    result = matcher.match(user=user, job=job)

    assert result.score == 1.0
    assert result.matched_skills == ["Python", "LangGraph"]
    # rule match reason uses "匹配" not "语义分析"
    assert "匹配" in result.reason
    assert "语义分析" not in result.reason


def test_matcher_falls_back_to_rule_when_llm_errors():
    def _broken_matcher(_payload):
        raise RuntimeError("API timeout")

    matcher = JobMatcher(llm_matcher=_broken_matcher)
    user = UserProfile(keyword="AI intern", skills=["Python"])
    job = make_job(skills=["FastAPI", "SQL"])
    # rule score = 0/2 = 0.0 < 0.6 → attempts LLM → fails → falls back

    result = matcher.match(user=user, job=job)

    assert result.score == 0.0  # rule result
    assert result.priority == "low"


# ── 折减分支边界（#27 修复的三个缺口）────────────────────────────────


def test_llm_missing_score_keeps_rule_score_without_discount_note():
    """LLM 返回缺 score 键：沿用规则分，不折减也不加折减标注（#27）。"""
    def no_score_llm(payload: dict) -> dict:
        return {"reason": "LLM 判断", "priority": "high"}  # 缺 score

    matcher = JobMatcher(llm_matcher=no_score_llm)
    user = UserProfile(keyword="AI", skills=["Python"], resume_text="")
    job = make_job(title="无关岗", skills=["测试", "Selenium", "Python"])

    result = matcher.match(user=user, job=job)

    assert result.score == pytest.approx(0.33)  # 规则分原样
    assert "折减" not in result.reason
    # priority 按规则分重算，不吃 LLM 自报的 high
    assert result.priority == "low"


def test_discount_never_below_rule_score():
    """规则 [0.4,0.6) 有真实关键词证据：折减不得把分数压到规则分以下（#27）。"""
    def llm(payload: dict) -> dict:
        return {"score": 0.7, "reason": "勉强", "priority": "medium"}

    matcher = JobMatcher(llm_matcher=llm)
    user = UserProfile(keyword="AI", skills=["Python", "大模型"], resume_text="")
    job = make_job(skills=["Python", "大模型", "SQL"])  # 规则 2/3 = 0.67? 不触发兜底
    # 需要规则分落在 [0.4, 0.6)：3 选 2 是 0.67，改 5 选 2 = 0.4
    job = make_job(skills=["Python", "大模型", "SQL", "风控", "爬虫"])

    result = matcher.match(user=user, job=job)

    assert result.score == pytest.approx(0.40)  # max(规则 0.4, 0.7*0.55=0.385)
    assert result.score >= 0.40  # 不得翻成 no_match
    assert "折减" in result.reason  # LLM 分确实被折了，标注保留


def test_discount_param_is_configurable():
    """llm_discount 构造参数可配置（此前全仓库无测试传入）。"""
    def llm(payload: dict) -> dict:
        return {"score": 0.8, "reason": "r", "priority": "high"}

    matcher = JobMatcher(llm_matcher=llm, llm_discount=1.0)
    user = UserProfile(keyword="AI", skills=["Python"], resume_text="")
    job = make_job(skills=["FastAPI", "SQL"])

    result = matcher.match(user=user, job=job)

    assert result.score == pytest.approx(0.8)  # 不折减
    assert "折减" not in result.reason
