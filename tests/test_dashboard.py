from web_task_agent.dashboard import HtmlDashboard
from web_task_agent.evaluation import EvaluationResult, TaskEvaluationResult
from web_task_agent.models import JobPosting, MatchResult, RunMetrics, UserProfile


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


def test_dashboard_renders_jobs_metrics_and_matches():
    dashboard = HtmlDashboard()
    user = UserProfile(keyword="AI intern", skills=["Python"])
    jobs = [make_job()]
    matches = [
        MatchResult(
            job_id="https://example.com/jobs/1",
            score=0.5,
            matched_skills=["Python"],
            missing_skills=["LLM"],
            priority="medium",
            suggested_actions=["补强或准备相关经历：LLM"],
        )
    ]
    metrics = RunMetrics(run_id="run-dashboard", pages_visited=1, jobs_found=1, valid_jobs=1)

    html = dashboard.render(user=user, jobs=jobs, matches=matches, metrics=metrics)

    assert "<!doctype html>" in html
    assert "Web Task Agent Dashboard" in html
    assert "AI Engineering Intern" in html
    assert "匹配分数" in html
    assert "0.50" in html
    assert "缺失技能" in html
    assert "LLM" in html
    assert "访问页面数" in html


def test_dashboard_escapes_html_content():
    dashboard = HtmlDashboard()
    user = UserProfile(keyword="<script>alert(1)</script>")
    jobs = [make_job(title="<b>AI</b>", company="Example <AI>")]
    metrics = RunMetrics(run_id="run-dashboard")

    html = dashboard.render(user=user, jobs=jobs, matches=[], metrics=metrics)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;b&gt;AI&lt;/b&gt;" in html
    assert "Example &lt;AI&gt;" in html


def test_dashboard_writes_html_file(tmp_path):
    dashboard = HtmlDashboard(output_dir=tmp_path)
    user = UserProfile(keyword="AI intern")
    metrics = RunMetrics(run_id="run-dashboard")

    path = dashboard.write_dashboard(
        user=user,
        jobs=[],
        matches=[],
        metrics=metrics,
    )

    assert path.exists()
    assert path.name == "run-dashboard.html"
    assert "未找到有效岗位" in path.read_text(encoding="utf-8")


def test_dashboard_renders_evaluation_summary_with_failure_counts():
    dashboard = HtmlDashboard()
    result = EvaluationResult(
        total_tasks=3,
        completed_tasks=1,
        success_rate=0.33,
        total_valid_jobs=2,
        average_pages_visited=1.67,
        failure_counts={"verification_filtered": 2},
        task_results=[
            TaskEvaluationResult(
                keyword="AI intern",
                location="Remote",
                pages_visited=1,
                valid_jobs=0,
                success=False,
                failure_reason="no valid jobs",
                failure_category="verification_filtered",
                failure_details="jobs_found=1; valid_jobs=0",
            )
        ],
    )

    html = dashboard.render_evaluation_summary(result)

    assert "<!doctype html>" in html
    assert "Evaluation Summary" in html
    assert "任务成功率" in html
    assert "0.33" in html
    assert "verification_filtered" in html
    assert "jobs_found=1; valid_jobs=0" in html
