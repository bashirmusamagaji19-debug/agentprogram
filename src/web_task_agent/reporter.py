from __future__ import annotations

from os.path import relpath
from pathlib import Path

from web_task_agent.models import JobPosting, MatchResult, RunMetrics, UserProfile
from web_task_agent.skill_gap import summarize_skill_gaps


class MarkdownReporter:
    def __init__(self, output_dir: str | Path = "reports"):
        self.output_dir = Path(output_dir)

    def write_report(
        self,
        *,
        user: UserProfile,
        jobs: list[JobPosting],
        matches: list[MatchResult] | None = None,
        metrics: RunMetrics,
        artifact_links: dict[str, str | Path] | None = None,
    ) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        report_path = self.output_dir / f"{metrics.run_id}.md"
        report_links = self._relative_artifact_links(report_path.parent, artifact_links)
        report_path.write_text(
            self.render(
                user=user,
                jobs=jobs,
                matches=matches,
                metrics=metrics,
                artifact_links=report_links,
            ),
            encoding="utf-8",
        )
        return report_path

    def render(
        self,
        *,
        user: UserProfile,
        jobs: list[JobPosting],
        matches: list[MatchResult] | None = None,
        metrics: RunMetrics,
        artifact_links: dict[str, str | Path] | None = None,
    ) -> str:
        match_by_job_id = {match.job_id: match for match in (matches or [])}
        lines = [
            "# AI 实习岗位搜索报告",
            "",
            "## 搜索条件",
            "",
            f"- 关键词: {user.keyword}",
            f"- 地点: {user.location}",
            f"- 目标数量: {user.target_count}",
            f"- 技能标签: {', '.join(user.skills) if user.skills else '未提供'}",
            "",
            "## 运行指标",
            "",
            f"- 访问页面数: {metrics.pages_visited}",
            f"- 发现岗位数: {metrics.jobs_found}",
            f"- 有效岗位数: {metrics.valid_jobs}",
            f"- 重复岗位数: {metrics.duplicate_jobs}",
            f"- 失败页面数: {metrics.failed_pages}",
            "",
            "## 岗位列表",
            "",
        ]

        if not jobs:
            lines.extend(["未找到有效岗位。", ""])
            self._append_artifact_links(lines, artifact_links)
            return "\n".join(lines)

        skill_gaps = summarize_skill_gaps(matches or [])
        if skill_gaps:
            lines.extend(["## 技能缺口汇总", ""])
            lines.extend(
                f"- {skill}: {count} 个岗位缺失" for skill, count in skill_gaps
            )
            lines.append("")

        for index, job in enumerate(jobs, start=1):
            lines.extend(
                [
                    f"### {index}. {job.title}",
                    "",
                    f"- 公司: {job.company}",
                    f"- 地点: {job.location}",
                    f"- 技能: {', '.join(job.skills) if job.skills else '未抽取'}",
                    f"- 置信度: {job.confidence:.2f}",
                    f"- 链接: {job.url}",
                    "",
                    "**岗位要求**",
                    "",
                    job.requirements or "未抽取",
                    "",
                    "**工作内容**",
                    "",
                    job.responsibilities or "未抽取",
                    "",
                ]
            )
            match = match_by_job_id.get(job.url)
            if match:
                lines.extend(
                    [
                        "## 匹配分析",
                        "",
                        f"- 匹配分数: {match.score:.2f}",
                        f"- 优先级: {match.priority}",
                        f"- 已匹配技能: {', '.join(match.matched_skills) if match.matched_skills else '暂无'}",
                        f"- 缺失技能: {', '.join(match.missing_skills) if match.missing_skills else '暂无'}",
                        f"- 匹配理由: {match.reason}",
                        "",
                        "**建议动作**",
                        "",
                    ]
                )
                lines.extend(f"- {action}" for action in match.suggested_actions)
                lines.append("")

        self._append_artifact_links(lines, artifact_links)
        return "\n".join(lines)

    def _append_artifact_links(
        self,
        lines: list[str],
        artifact_links: dict[str, str | Path] | None,
    ) -> None:
        if not artifact_links:
            return
        lines.extend(["## 相关产物", ""])
        for label, path in artifact_links.items():
            href = Path(path).as_posix()
            lines.append(f"- {label}: [{href}]({href})")
        lines.append("")

    def _relative_artifact_links(
        self,
        base_dir: Path,
        artifact_links: dict[str, str | Path] | None,
    ) -> dict[str, str] | None:
        if not artifact_links:
            return None
        base = base_dir.resolve()
        return {
            label: relpath(Path(path).resolve(), start=base).replace("\\", "/")
            for label, path in artifact_links.items()
        }
