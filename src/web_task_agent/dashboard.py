from __future__ import annotations

from html import escape
from pathlib import Path

from web_task_agent.evaluation import EvaluationResult, TaskEvaluationResult
from web_task_agent.models import JobPosting, MatchResult, RunMetrics, UserProfile
from web_task_agent.skill_gap import summarize_skill_gaps


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
        search_queries: list[str] | None = None,
        failed_url_errors: list[dict[str, str]] | None = None,
        artifact_links: dict[str, str | Path] | None = None,
    ) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{metrics.run_id}.html"
        path.write_text(
            self.render(
                user=user,
                jobs=jobs,
                matches=matches,
                metrics=metrics,
                search_queries=search_queries,
                failed_url_errors=failed_url_errors,
                artifact_links=artifact_links,
            ),
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
        search_queries: list[str] | None = None,
        failed_url_errors: list[dict[str, str]] | None = None,
        artifact_links: dict[str, str | Path] | None = None,
    ) -> str:
        match_by_job_id = {match.job_id: match for match in matches}
        job_rows = "\n".join(
            self._job_row(job, match_by_job_id.get(job.url)) for job in jobs
        )
        if not job_rows:
            job_rows = '<tr><td colspan="7">未找到有效岗位</td></tr>'
        input_trace = self._input_trace(
            user=user,
            search_queries=search_queries or [],
            failed_url_errors=failed_url_errors or [],
        )
        gap_summary = self._skill_gap_summary(matches)
        artifacts = self._artifact_links(artifact_links)

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
    .controls {{
      display: grid;
      grid-template-columns: minmax(220px, 1fr) repeat(2, minmax(160px, 220px));
      gap: 12px;
      align-items: end;
      margin-bottom: 14px;
    }}
    .control label {{
      display: block;
      margin-bottom: 6px;
      color: var(--muted);
      font-size: 13px;
    }}
    .control input,
    .control select {{
      box-sizing: border-box;
      width: 100%;
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      padding: 8px 10px;
      font: inherit;
    }}
    .result-count {{
      margin: 0 0 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    .gap-summary {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 18px;
    }}
    .gap-summary h2 {{
      margin: 0 0 10px;
      font-size: 16px;
    }}
    .gap-summary ul {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      list-style: none;
      margin: 0;
      padding: 0;
    }}
    .gap-summary li {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 6px 8px;
      background: #f9fafb;
      font-size: 13px;
    }}
    @media (max-width: 760px) {{
      .controls {{
        grid-template-columns: 1fr;
      }}
    }}
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
    {input_trace}
    {artifacts}
    {gap_summary}
    <section class="controls" aria-label="Dashboard controls">
      <div class="control">
        <label for="job-search">搜索岗位</label>
        <input id="job-search" type="search" placeholder="岗位、公司、地点或技能">
      </div>
      <div class="control">
        <label for="priority-filter">优先级</label>
        <select id="priority-filter">
          <option value="all">全部</option>
          <option value="high">high</option>
          <option value="medium">medium</option>
          <option value="low">low</option>
        </select>
      </div>
      <div class="control">
        <label for="sort-by">排序</label>
        <select id="sort-by">
          <option value="score-desc">匹配分数从高到低</option>
          <option value="score-asc">匹配分数从低到高</option>
          <option value="title-asc">岗位标题 A-Z</option>
        </select>
      </div>
    </section>
    <p class="result-count">当前显示 <span id="visible-count">{len(jobs)}</span> 个岗位</p>
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
      <tbody id="job-rows">
        {job_rows}
      </tbody>
    </table>
  </main>
  <script>
    function applyDashboardFilters() {{
      const query = document.getElementById("job-search").value.toLowerCase().trim();
      const priority = document.getElementById("priority-filter").value;
      const sortBy = document.getElementById("sort-by").value;
      const body = document.getElementById("job-rows");
      const rows = Array.from(body.querySelectorAll("tr[data-search]"));

      rows.sort((left, right) => {{
        if (sortBy === "score-asc") {{
          return Number(left.dataset.score) - Number(right.dataset.score);
        }}
        if (sortBy === "title-asc") {{
          return left.dataset.title.localeCompare(right.dataset.title);
        }}
        return Number(right.dataset.score) - Number(left.dataset.score);
      }});

      let visible = 0;
      for (const row of rows) {{
        const matchesQuery = !query || row.dataset.search.includes(query);
        const matchesPriority = priority === "all" || row.dataset.priority === priority;
        const show = matchesQuery && matchesPriority;
        row.hidden = !show;
        if (show) {{
          visible += 1;
        }}
        body.appendChild(row);
      }}
      document.getElementById("visible-count").textContent = String(visible);
    }}

    for (const control of ["job-search", "priority-filter", "sort-by"]) {{
      document.getElementById(control).addEventListener("input", applyDashboardFilters);
    }}
    applyDashboardFilters();
  </script>
</body>
</html>
"""

    def _job_row(self, job: JobPosting, match: MatchResult | None) -> str:
        score = f"{match.score:.2f}" if match else "暂无"
        score_value = f"{match.score:.2f}" if match else "0.00"
        priority = match.priority if match else "low"
        missing = self._skills(match.missing_skills) if match else "暂无"
        search_terms = [
            job.title,
            job.company,
            job.location,
            *job.skills,
            *(match.matched_skills if match else []),
            *(match.missing_skills if match else []),
        ]
        seen_terms: set[str] = set()
        search_text_parts: list[str] = []
        for term in search_terms:
            key = term.casefold()
            if key not in seen_terms:
                seen_terms.add(key)
                search_text_parts.append(key)
        search_text = " ".join(search_text_parts)
        return f"""<tr data-score="{score_value}" data-priority="{escape(priority)}" data-title="{escape(job.title.casefold())}" data-search="{escape(search_text)}">
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

    def _skill_gap_summary(self, matches: list[MatchResult]) -> str:
        gaps = summarize_skill_gaps(matches)
        if not gaps:
            return ""
        items = "\n".join(
            f"<li>{escape(skill)}: {count} 个岗位</li>" for skill, count in gaps
        )
        return f"""<section class="gap-summary">
      <h2>技能缺口汇总</h2>
      <ul>{items}</ul>
    </section>"""

    def _input_trace(
        self,
        *,
        user: UserProfile,
        search_queries: list[str],
        failed_url_errors: list[dict[str, str]],
    ) -> str:
        trace_parts: list[str] = []
        if user.seed_urls:
            items = "\n".join(
                f'<li><a href="{escape(url)}">{escape(url)}</a></li>'
                for url in user.seed_urls
            )
            trace_parts.append(f"<p>Seed URL mode</p><ul>{items}</ul>")
        if search_queries:
            items = "\n".join(
                f"<li>{escape(query)}</li>" for query in search_queries
            )
            trace_parts.append(f"<p>Search query mode</p><ul>{items}</ul>")
        if failed_url_errors:
            items = "\n".join(
                "<li>"
                f'<a href="{escape(item.get("url", ""))}">{escape(item.get("url", "-"))}</a>'
                f" -> {escape(item.get('error', '-'))}"
                "</li>"
                for item in failed_url_errors
            )
            trace_parts.append(f"<p>URL Errors</p><ul>{items}</ul>")
        if not trace_parts:
            return ""
        return f"""<section class="gap-summary">
      <h2>Input Trace</h2>
      {''.join(trace_parts)}
    </section>"""

    def _artifact_links(self, artifact_links: dict[str, str | Path] | None) -> str:
        if not artifact_links:
            return ""
        items = "\n".join(
            f'<li><a href="{escape(Path(path).as_posix())}">{escape(label)}</a></li>'
            for label, path in artifact_links.items()
        )
        return f"""<section class="gap-summary">
      <h2>相关产物</h2>
      <ul>{items}</ul>
    </section>"""

    def render_evaluation_summary(self, result: EvaluationResult) -> str:
        failure_rows = "\n".join(
            f"<tr><td>{escape(category)}</td><td>{count}</td></tr>"
            for category, count in sorted(result.failure_counts.items())
        )
        if not failure_rows:
            failure_rows = "<tr><td>-</td><td>0</td></tr>"

        task_rows = "\n".join(
            self._evaluation_task_row(task_result)
            for task_result in result.task_results
        )
        if not task_rows:
            task_rows = '<tr><td colspan="8">暂无评测任务</td></tr>'

        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Evaluation Summary</title>
</head>
<body>
  <main>
    <h1>Evaluation Summary</h1>
    <section class="metrics">
      {self._metric("任务总数", result.total_tasks)}
      {self._metric("完成任务数", result.completed_tasks)}
      {self._metric("任务成功率", f"{result.success_rate:.2f}")}
      {self._metric("有效岗位总数", result.total_valid_jobs)}
      {self._metric("平均访问页面数", f"{result.average_pages_visited:.2f}")}
    </section>
    <h2>失败原因分布</h2>
    <table>
      <thead><tr><th>类别</th><th>数量</th></tr></thead>
      <tbody>{failure_rows}</tbody>
    </table>
    <h2>任务明细</h2>
    <table>
      <thead>
        <tr>
          <th>关键词</th>
          <th>地点</th>
          <th>访问页面数</th>
          <th>有效岗位数</th>
          <th>状态</th>
          <th>失败类别</th>
          <th>失败原因</th>
          <th>失败细节</th>
        </tr>
      </thead>
      <tbody>{task_rows}</tbody>
    </table>
  </main>
</body>
</html>
"""

    def _evaluation_task_row(self, task_result: TaskEvaluationResult) -> str:
        status = "成功" if task_result.success else "失败"
        return f"""<tr>
  <td>{escape(task_result.keyword)}</td>
  <td>{escape(task_result.location)}</td>
  <td>{task_result.pages_visited}</td>
  <td>{task_result.valid_jobs}</td>
  <td>{status}</td>
  <td>{escape(task_result.failure_category or "-")}</td>
  <td>{escape(task_result.failure_reason or "-")}</td>
  <td>{escape(task_result.failure_details or "-")}</td>
</tr>"""
