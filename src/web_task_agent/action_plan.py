from __future__ import annotations

from pathlib import Path

from web_task_agent.models import JobPosting, MatchResult, UserProfile
from web_task_agent.skill_gap import summarize_skill_gaps


class ActionPlanWriter:
    def __init__(self, output_dir: str | Path = "action-plans") -> None:
        self.output_dir = Path(output_dir)

    def write_plan(
        self,
        *,
        run_id: str,
        user: UserProfile,
        jobs: list[JobPosting],
        matches: list[MatchResult],
    ) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{run_id}.md"
        path.write_text(
            self.render(user=user, jobs=jobs, matches=matches),
            encoding="utf-8",
        )
        return path

    def render(
        self,
        *,
        user: UserProfile,
        jobs: list[JobPosting],
        matches: list[MatchResult],
    ) -> str:
        match_by_job_id = {match.job_id: match for match in matches}
        lines = [
            "# AI 实习行动计划",
            "",
            "## 目标画像",
            "",
            f"- 岗位方向: {user.keyword}",
            f"- 地点: {user.location}",
            f"- 已有技能: {', '.join(user.skills) if user.skills else '未提供'}",
            "",
            "## 优先投递岗位",
            "",
        ]
        lines.extend(self._priority_job_lines(jobs, match_by_job_id))
        lines.extend(["", "## 技能补强顺序", ""])
        gaps = summarize_skill_gaps(matches)
        if gaps:
            lines.extend(f"- {skill}: {count} 个岗位缺失" for skill, count in gaps)
        else:
            lines.append("- 暂无明显技能缺口，优先打磨项目描述和投递材料。")
        lines.extend(["", "## 补强项目任务", ""])
        lines.extend(self._project_tasks(gaps))
        lines.append("")
        return "\n".join(lines)

    def _priority_job_lines(
        self,
        jobs: list[JobPosting],
        match_by_job_id: dict[str, MatchResult],
    ) -> list[str]:
        ranked_jobs = sorted(
            jobs,
            key=lambda job: self._score_for_job(job, match_by_job_id),
            reverse=True,
        )
        if not ranked_jobs:
            return ["- 暂无有效岗位，先检查搜索词或 seed URL。"]
        lines: list[str] = []
        for index, job in enumerate(ranked_jobs[:5], start=1):
            match = match_by_job_id.get(job.url)
            score = match.score if match else 0.0
            priority = match.priority if match else "low"
            lines.append(
                f"{index}. {job.title} - {job.company} ({priority}, 匹配 {score:.2f})"
            )
            lines.append(f"   - 链接: {job.url}")
        return lines

    def _score_for_job(
        self,
        job: JobPosting,
        match_by_job_id: dict[str, MatchResult],
    ) -> float:
        match = match_by_job_id.get(job.url)
        return match.score if match else 0.0

    def _project_tasks(self, gaps: list[tuple[str, int]]) -> list[str]:
        if not gaps:
            return ["- 用现有项目写一版 STAR 结构投递故事。"]
        return [
            f"- 围绕 {skill} 补一个可展示任务：实现、评测、文档和 demo 截图各一份。"
            for skill, _count in gaps[:5]
        ]
