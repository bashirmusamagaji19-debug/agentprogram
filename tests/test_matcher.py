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
