from pathlib import Path

from web_task_agent.models import JobPosting, MatchResult, RunMetrics, UserProfile
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


def test_reporter_renders_match_results(tmp_path):
    reporter = MarkdownReporter(output_dir=tmp_path)
    user = UserProfile(keyword="AI intern", skills=["Python", "LangGraph"])
    jobs = [make_job()]
    metrics = RunMetrics(run_id="run-match", valid_jobs=1)
    matches = [
        MatchResult(
            job_id="https://example.com/jobs/1",
            score=0.5,
            matched_skills=["Python", "LangGraph"],
            missing_skills=["LLM", "FastAPI"],
            priority="medium",
            reason="匹配 2/4 个岗位技能。",
            suggested_actions=["补强或准备相关经历：LLM"],
        )
    ]

    content = reporter.render(
        user=user,
        jobs=jobs,
        metrics=metrics,
        matches=matches,
    )

    assert "## 匹配分析" in content
    assert "匹配分数: 0.50" in content
    assert "优先级: medium" in content
    assert "已匹配技能: Python, LangGraph" in content
    assert "缺失技能: LLM, FastAPI" in content


def test_reporter_renders_skill_gap_summary(tmp_path):
    reporter = MarkdownReporter(output_dir=tmp_path)
    user = UserProfile(keyword="AI intern", skills=["Python"])
    jobs = [
        make_job(url="https://example.com/jobs/1"),
        make_job(url="https://example.com/jobs/2"),
    ]
    metrics = RunMetrics(run_id="run-gap", valid_jobs=2)
    matches = [
        MatchResult(
            job_id="https://example.com/jobs/1",
            score=0.5,
            missing_skills=["LLM", "FastAPI"],
        ),
        MatchResult(
            job_id="https://example.com/jobs/2",
            score=0.4,
            missing_skills=["LLM"],
        ),
    ]

    content = reporter.render(
        user=user,
        jobs=jobs,
        metrics=metrics,
        matches=matches,
    )

    assert "## 技能缺口汇总" in content
    assert "- LLM: 2 个岗位缺失" in content
    assert "- FastAPI: 1 个岗位缺失" in content


def test_reporter_renders_related_artifact_links(tmp_path):
    reporter = MarkdownReporter(output_dir=tmp_path)
    user = UserProfile(keyword="AI intern", skills=["Python"])
    jobs = [make_job()]
    metrics = RunMetrics(run_id="run-artifacts", valid_jobs=1)

    content = reporter.render(
        user=user,
        jobs=jobs,
        metrics=metrics,
        artifact_links={"行动计划": Path("action-plans/run-artifacts.md")},
    )

    assert "## 相关产物" in content
    assert "- 行动计划: [action-plans/run-artifacts.md](action-plans/run-artifacts.md)" in content


def test_reporter_writes_relative_artifact_links(tmp_path):
    reporter = MarkdownReporter(output_dir=tmp_path / "reports")
    user = UserProfile(keyword="AI intern", skills=["Python"])
    jobs = [make_job()]
    metrics = RunMetrics(run_id="run-artifacts", valid_jobs=1)
    action_plan_path = tmp_path / "action-plans" / "run-artifacts.md"

    report_path = reporter.write_report(
        user=user,
        jobs=jobs,
        metrics=metrics,
        artifact_links={"行动计划": action_plan_path},
    )

    content = report_path.read_text(encoding="utf-8")
    assert (
        "- 行动计划: [../action-plans/run-artifacts.md](../action-plans/run-artifacts.md)"
        in content
    )


def test_reporter_renders_agent_execution_trace(tmp_path):
    reporter = MarkdownReporter(output_dir=tmp_path)
    user = UserProfile(keyword="AI intern", skills=["Python"])
    metrics = RunMetrics(run_id="run-trace", valid_jobs=1)

    content = reporter.render(
        user=user,
        jobs=[make_job()],
        metrics=metrics,
        execution_trace=[
            {"node": "planner", "summary": "planned 3 search queries"},
            {"node": "browser", "summary": "visited 2 pages"},
        ],
    )

    assert "## Agent 执行轨迹" in content
    assert "- planner: planned 3 search queries" in content
    assert "- browser: visited 2 pages" in content


def test_reporter_renders_interview_talking_points(tmp_path):
    reporter = MarkdownReporter(output_dir=tmp_path)
    user = UserProfile(keyword="AI intern", skills=["Python"])
    metrics = RunMetrics(run_id="run-story", valid_jobs=1)

    content = reporter.render(
        user=user,
        jobs=[make_job()],
        metrics=metrics,
        execution_trace=[
            {"node": "planner", "summary": "planned 3 search queries"},
            {"node": "browser", "summary": "visited 2 pages"},
        ],
        orchestration_mode="langgraph",
    )

    assert "## 面试讲述要点" in content
    assert "BrowserClient" in content
    assert "LangGraph" in content
    assert "Agent 执行轨迹" in content
    assert "评测" in content


def test_reporter_renders_source_quality_and_per_job_evidence(tmp_path):
    reporter = MarkdownReporter(output_dir=tmp_path)
    user = UserProfile(keyword="AI intern", location="Remote", target_count=1)
    metrics = RunMetrics(run_id="run-source", valid_jobs=1)
    content = reporter.render(
        user=user,
        jobs=[make_job()],
        metrics=metrics,
        run_details={
            "source_manifest": "source-archive/run-source/manifest.json",
            "source_mode": "demo",
            "source_captured_at": "2026-10-23T10:00:00+00:00",
            "source_metrics": {
                "source_callable": {"numerator": 1, "denominator": 1, "value": 1.0},
                "detail_body_fetch": {"numerator": 1, "denominator": 1, "value": 1.0},
            },
            "source_evidence": {
                "https://example.com/jobs/1": {
                    "evidence_level": "detail",
                    "content_origin": "http",
                    "availability": "observed",
                    "observed_at": "2026-10-23T10:00:00+00:00",
                }
            },
        },
    )

    assert "来源质量" in content
    assert "来源可调用率: 1/1 (100.0%)" in content
    assert "详情正文获取率: 1/1 (100.0%)" in content
    assert "证据层级: detail" in content
    assert "内容来源: http" in content


def test_reporter_renders_link_receipt_without_upgrading_missing_receipts(tmp_path):
    reporter = MarkdownReporter(output_dir=tmp_path)
    user = UserProfile(keyword="AI intern", location="Remote", target_count=2)
    metrics = RunMetrics(run_id="run-link", valid_jobs=2)
    verified = make_job(
        url="https://example.com/jobs/verified",
        url_verification="api-verified",
    ).model_copy(
        update={
            "link_verification": {
                "status": "verified",
                "checked_at": "2026-10-26T08:30:00+00:00",
                "run_id": "run-link",
                "requested_url": "https://example.com/jobs/verified",
                "final_url": "https://example.com/jobs/verified-final",
                "method": "GET",
                "reason": "title and JD matched",
                "evidence_sha256": "a" * 64,
                "title_matched": True,
                "jd_matched": True,
                "active": True,
            }
        }
    )
    legacy = make_job(url="https://example.com/jobs/legacy")
    pending = make_job(url="https://example.com/jobs/pending").model_copy(
        update={
            "link_verification": {
                "status": "pending",
                "checked_at": "2026-10-26T08:31:00+00:00",
                "run_id": "run-link",
                "requested_url": "https://example.com/jobs/pending",
                "final_url": "",
                "method": "GET",
                "reason": "active status unknown",
                "evidence_sha256": "b" * 64,
                "title_matched": True,
                "jd_matched": False,
                "active": None,
            }
        }
    )

    content = reporter.render(
        user=user,
        jobs=[verified, legacy],
        metrics=metrics,
        run_details={"pending_jobs": [pending.model_dump(mode="json")]},
    )

    assert "链接核验: ✓ 已核验" in content
    assert "核验时间: 2026-10-26T08:30:00+00:00" in content
    assert "核验方式: GET" in content
    assert "最终URL: https://example.com/jobs/verified-final" in content
    assert "链接核验: 未核验（无核验记录）" in content
    assert "网页已验证" not in content
    assert "## 未交付候选" in content
    assert "pending_jobs" in content
    assert '"status": "pending"' in content
    assert "active status unknown" in content


def test_reporter_keeps_pending_json_when_no_delivered_jobs(tmp_path):
    reporter = MarkdownReporter(output_dir=tmp_path)
    user = UserProfile(keyword="AI intern")
    metrics = RunMetrics(run_id="run-pending-only", valid_jobs=0)

    content = reporter.render(
        user=user,
        jobs=[],
        metrics=metrics,
        run_details={
            "pending_jobs": [
                {
                    "title": "Pending Intern",
                    "url": "https://example.com/jobs/pending-only",
                    "link_verification": {
                        "status": "pending",
                        "reason": "active status unknown",
                    },
                }
            ]
        },
    )

    assert "## 未交付候选" in content
    assert "未找到有效岗位" in content
    assert "pending-only" in content
    assert "active status unknown" in content
