from __future__ import annotations

from html import escape
from pathlib import Path

from web_task_agent.models import JobPosting, MatchResult, RunMetrics, UserProfile


class HtmlDashboard:
    def __init__(self, output_dir: str | Path = "dashboards") -> None:
        self.output_dir = Path(output_dir)

    def write_dashboard(
        self,
        *,
        user: UserProfile,
        jobs: list[JobPosting],
        matches: list[MatchResult],
        metrics: RunMetrics,
    ) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{metrics.run_id}.html"
        path.write_text(
            self.render(user=user, jobs=jobs, matches=matches, metrics=metrics),
            encoding="utf-8",
        )
        return path

    def render(
        self,
        *,
        user: UserProfile,
        jobs: list[JobPosting],
        matches: list[MatchResult],
        metrics: RunMetrics,
    ) -> str:
        match_by_job_id = {match.job_id: match for match in matches}
        job_rows = "\n".join(
            self._job_row(job, match_by_job_id.get(job.url)) for job in jobs
        )
        if not job_rows:
            job_rows = '<tr><td colspan="7">未找到有效岗位</td></tr>'

        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Web Task Agent Dashboard</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2937;
      --muted: #667085;
      --line: #d9dee7;
      --accent: #2563eb;
      --good: #0f766e;
      --warn: #b45309;
      --low: #b91c1c;
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      line-height: 1.5;
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
      letter-spacing: 0;
    }}
    .subtitle {{
      color: var(--muted);
      margin-bottom: 24px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
      margin-bottom: 24px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .metric span {{
      display: block;
      color: var(--muted);
      font-size: 13px;
    }}
    .metric strong {{
      font-size: 24px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    th, td {{
      padding: 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      font-size: 13px;
      color: var(--muted);
      background: #f9fafb;
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
    }}
    .pill {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      background: #eef2ff;
      color: #3730a3;
      margin: 0 4px 4px 0;
      font-size: 12px;
    }}
    .priority-high {{ color: var(--good); font-weight: 700; }}
    .priority-medium {{ color: var(--warn); font-weight: 700; }}
    .priority-low {{ color: var(--low); font-weight: 700; }}
  </style>
</head>
<body>
  <main>
    <h1>Web Task Agent Dashboard</h1>
    <div class="subtitle">关键词：{escape(user.keyword)} · 地点：{escape(user.location)} · 技能：{self._skills(user.skills)}</div>
    <section class="metrics">
      {self._metric("访问页面数", metrics.pages_visited)}
      {self._metric("发现岗位数", metrics.jobs_found)}
      {self._metric("有效岗位数", metrics.valid_jobs)}
      {self._metric("重复岗位数", metrics.duplicate_jobs)}
      {self._metric("失败页面数", metrics.failed_pages)}
    </section>
    <table>
      <thead>
        <tr>
          <th>岗位</th>
          <th>公司 / 地点</th>
          <th>技能</th>
          <th>匹配分数</th>
          <th>优先级</th>
          <th>缺失技能</th>
          <th>链接</th>
        </tr>
      </thead>
      <tbody>
        {job_rows}
      </tbody>
    </table>
  </main>
</body>
</html>
"""

    def _job_row(self, job: JobPosting, match: MatchResult | None) -> str:
        score = f"{match.score:.2f}" if match else "暂无"
        priority = match.priority if match else "low"
        missing = self._skills(match.missing_skills) if match else "暂无"
        return f"""<tr>
  <td>{escape(job.title)}</td>
  <td>{escape(job.company)}<br><span>{escape(job.location)}</span></td>
  <td>{self._skills(job.skills)}</td>
  <td>{score}</td>
  <td class="priority-{escape(priority)}">{escape(priority)}</td>
  <td>{missing}</td>
  <td><a href="{escape(job.url)}">打开</a></td>
</tr>"""

    def _metric(self, label: str, value: int | float) -> str:
        return f'<div class="metric"><span>{escape(label)}</span><strong>{value}</strong></div>'

    def _skills(self, skills: list[str]) -> str:
        if not skills:
            return "暂无"
        return " ".join(f'<span class="pill">{escape(skill)}</span>' for skill in skills)
