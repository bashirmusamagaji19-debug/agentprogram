from web_task_agent.action_plan import ActionPlanWriter
from web_task_agent.models import JobPosting, MatchResult, UserProfile


def make_job(**overrides):
    data = {
        "title": "AI Engineering Intern",
        "company": "Example AI",
        "location": "Remote",
        "source": "fixture",
        "url": "https://example.com/jobs/1",
        "requirements": "Python, LLM",
        "responsibilities": "Build agents",
        "skills": ["Python", "LLM"],
        "confidence": 0.9,
    }
    data.update(overrides)
    return JobPosting(**data)


def test_action_plan_renders_priority_jobs_skill_gaps_and_project_tasks():
    user = UserProfile(keyword="AI intern", skills=["Python"])
    jobs = [
        make_job(title="AI Agent Intern", url="https://example.com/jobs/agent"),
        make_job(
            title="LLM Platform Intern",
            company="DataWorks",
            url="https://example.com/jobs/platform",
            skills=["Python", "FastAPI", "LLM"],
        ),
    ]
    matches = [
        MatchResult(
            job_id="https://example.com/jobs/agent",
            score=0.5,
            missing_skills=["LLM"],
            priority="medium",
        ),
        MatchResult(
            job_id="https://example.com/jobs/platform",
            score=0.33,
            missing_skills=["FastAPI", "LLM"],
            priority="low",
        ),
    ]

    markdown = ActionPlanWriter().render(user=user, jobs=jobs, matches=matches)

    assert "# AI 实习行动计划" in markdown
    assert "AI Agent Intern" in markdown
    assert "https://example.com/jobs/agent" in markdown
    assert "- LLM: 2 个岗位缺失" in markdown
    assert "- FastAPI: 1 个岗位缺失" in markdown
    assert "补强项目任务" in markdown
    assert "## 7 天执行节奏" in markdown
    assert "Day 1" in markdown
    assert "Day 2-4" in markdown
    assert "Day 7" in markdown
    assert "LLM" in markdown
    assert "FastAPI" in markdown


def test_action_plan_uses_generic_execution_rhythm_without_skill_gaps():
    user = UserProfile(keyword="AI intern", skills=["Python", "LLM"])
    jobs = [make_job()]
    matches = [
        MatchResult(
            job_id="https://example.com/jobs/1",
            score=1.0,
            matched_skills=["Python", "LLM"],
            missing_skills=[],
            priority="high",
        )
    ]

    markdown = ActionPlanWriter().render(user=user, jobs=jobs, matches=matches)

    assert "## 7 天执行节奏" in markdown
    assert "Day 2-4: 用现有项目补充评测、README、截图和面试讲解稿。" in markdown
