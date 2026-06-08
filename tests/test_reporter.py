from web_task_agent.models import JobPosting, RunMetrics, UserProfile
from web_task_agent.reporter import MarkdownReporter


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


def test_reporter_writes_markdown_report(tmp_path):
    reporter = MarkdownReporter(output_dir=tmp_path)
    user = UserProfile(
        keyword="AI intern",
        location="Remote",
        target_count=1,
        skills=["Python"],
    )
    jobs = [make_job()]
    metrics = RunMetrics(run_id="run-1", pages_visited=1, jobs_found=1, valid_jobs=1)

    report_path = reporter.write_report(user=user, jobs=jobs, metrics=metrics)

    content = report_path.read_text(encoding="utf-8")
    assert "# AI 实习岗位搜索报告" in content
    assert "AI Engineering Intern" in content
    assert "https://example.com/jobs/1" in content
    assert "有效岗位数: 1" in content


def test_reporter_renders_missing_skills_and_empty_text(tmp_path):
    reporter = MarkdownReporter(output_dir=tmp_path)
    user = UserProfile(keyword="AI intern", location="Remote", target_count=1)
    jobs = [
        make_job(
            skills=[],
            requirements="",
            responsibilities="",
            confidence=0.5,
        )
    ]
    metrics = RunMetrics(run_id="run-2")

    content = reporter.render(user=user, jobs=jobs, metrics=metrics)

    assert "技能标签: 未提供" in content
    assert "技能: 未抽取" in content
    assert content.count("未抽取") >= 3


def test_reporter_creates_output_directory(tmp_path):
    output_dir = tmp_path / "nested" / "reports"
    reporter = MarkdownReporter(output_dir=output_dir)
    user = UserProfile(keyword="AI intern")
    metrics = RunMetrics(run_id="run-3")

    report_path = reporter.write_report(user=user, jobs=[], metrics=metrics)

    assert report_path.exists()
    assert report_path.parent == output_dir
