from __future__ import annotations

import json
from collections.abc import Mapping
from os.path import relpath
from pathlib import Path
from typing import Any

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
        execution_trace: list[dict[str, str]] | None = None,
        orchestration_mode: str = "sequential",
        run_details: dict[str, Any] | None = None,
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
                execution_trace=execution_trace,
                orchestration_mode=orchestration_mode,
                run_details=run_details,
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
        execution_trace: list[dict[str, str]] | None = None,
        orchestration_mode: str = "sequential",
        run_details: dict[str, Any] | None = None,
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
            f"- 编排模式: {orchestration_mode}",
            "",
        ]

        if run_details:
            usage = run_details.get("model_usage", [])
            tokens = (sum(record["total_tokens"] for record in usage)
                      if run_details.get("model_usage_complete", not usage)
                      and all(record.get("total_tokens") is not None for record in usage) else None)
            lines.extend([
                "## 执行状态", "",
                f"- 运行编号: {metrics.run_id}",
                f"- 状态: {run_details.get('terminal_status', 'unknown')}",
                f"- 结束原因: {run_details.get('terminal_reason', 'unknown')}",
                f"- 唯一候选数: {run_details.get('unique_candidates', 0)}",
                f"- 打开成功/失败次数: {run_details.get('open_successes', 0)}/{run_details.get('open_failures', 0)}",
                f"- 工具耗时 ms: {run_details.get('tool_latency_ms', 0):.2f}",
                f"- 活跃执行耗时 ms: {run_details.get('runtime_latency_ms', 0):.2f}",
                f"- 审批等待 ms: {run_details.get('approval_wait_ms', 0):.2f}",
                f"- 模型 Token: {tokens if tokens is not None else '未知（缺少 usage）'}", "",
            ])
            audit = run_details.get("approval_audit", [])
            if run_details.get("source_manifest"):
                lines.extend(["## 来源归档", "",
                              f"- 模式: {run_details.get('source_mode', 'unknown')}",
                              f"- 采集会话时间: {run_details.get('source_captured_at', 'unknown')}",
                              f"- Manifest: {run_details['source_manifest']}", ""])
                source_metrics = run_details.get("source_metrics")
                if isinstance(source_metrics, Mapping):
                    lines.extend([
                        "### 来源质量",
                        "",
                        f"- 来源可调用率: {self._ratio_text(source_metrics.get('source_callable'))}",
                        f"- 有候选来源率: {self._ratio_text(source_metrics.get('sources_with_candidates'))}",
                        f"- 详情正文获取率: {self._ratio_text(source_metrics.get('detail_body_fetch'))}",
                        f"- 条件通过率: {self._ratio_text(source_metrics.get('conditions_passed'))}",
                        f"- 快照完整率: {self._ratio_text(source_metrics.get('snapshot_integrity'))}",
                        f"- 来源交付层级: {source_metrics.get('deliveries_by_origin', {})}",
                        "",
                    ])
            if audit:
                lines.extend(["## 审批记录", ""])
                for event in audit:
                    lines.append(f"- {event['approval_id']}: {event['event']} / {event.get('outcome') or '-'}")
                lines.append("")

        if run_details and "pending_jobs" in run_details:
            self._append_pending_jobs(lines, run_details.get("pending_jobs"))

        lines.extend(["## 岗位列表", ""])

        if not jobs:
            lines.extend(["未找到有效岗位。", ""])
            self._append_artifact_links(lines, artifact_links)
            return "\n".join(lines)

        skill_gaps = summarize_skill_gaps(matches or [])
        if skill_gaps:
            lines.extend(["## 技能缺口汇总", ""])
            lines.extend(f"- {skill}: {count} 个岗位缺失" for skill, count in skill_gaps)
            lines.append("")

        self._append_execution_trace(lines, execution_trace)
        self._append_interview_talking_points(lines, orchestration_mode)

        for index, job in enumerate(jobs, start=1):
            source_evidence = (run_details or {}).get("source_evidence", {})
            evidence = (
                source_evidence.get(job.url, {})
                if isinstance(source_evidence, Mapping)
                else {}
            )
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
            receipt = self._link_receipt(job)
            lines.extend(
                [
                    f"- 正文来源: {self._body_source_text(job)}",
                    f"- 链接核验: {self._link_status_text(receipt)}",
                    f"- 核验时间: {self._receipt_text(receipt, 'checked_at')}",
                    f"- 核验方式: {self._receipt_text(receipt, 'method')}",
                    f"- 最终URL: {self._receipt_text(receipt, 'final_url')}",
                ]
            )
            if receipt:
                lines.extend(
                    [
                        f"- 核验原因: {self._receipt_text(receipt, 'reason')}",
                        f"- 标题匹配: {self._matched_text(receipt.get('title_matched'))}",
                        f"- JD 匹配: {self._matched_text(receipt.get('jd_matched'))}",
                        f"- 证据 SHA256: {self._receipt_text(receipt, 'evidence_sha256')}",
                    ]
                )
            lines.append("")
            if isinstance(evidence, Mapping) and evidence:
                lines.extend([
                    f"- 证据层级: {evidence.get('evidence_level', 'unknown')}",
                    f"- 内容来源: {evidence.get('content_origin', 'unknown')}",
                    f"- 本次取用方式: {evidence.get('acquisition_origin', 'unknown')}",
                    f"- 当前可用性: {evidence.get('availability', 'unknown')}",
                    f"- 内容观察时间: {evidence.get('observed_at', 'unknown')}",
                    "",
                ])
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

    def _append_pending_jobs(self, lines: list[str], pending_value: Any) -> None:
        pending = pending_value if isinstance(pending_value, list) else []
        serialized = [self._jsonable(item) for item in pending]
        lines.extend(
            [
                "## 未交付候选",
                "",
                f"- 数量: {len(serialized)}",
                "- 下列候选未通过或尚未完成链接核验，不计入已交付岗位或岗位库保存结果。",
                "",
                "### pending_jobs",
                "",
                "```json",
                json.dumps(serialized, ensure_ascii=False, indent=2, default=str),
                "```",
                "",
            ]
        )

    @staticmethod
    def _jsonable(value: Any) -> Any:
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return model_dump(mode="json")
        if isinstance(value, Mapping):
            return {str(key): MarkdownReporter._jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [MarkdownReporter._jsonable(item) for item in value]
        return value

    @staticmethod
    def _link_receipt(job: JobPosting) -> dict[str, Any]:
        value = getattr(job, "link_verification", {})
        if isinstance(value, Mapping):
            return dict(value)
        return {}

    @staticmethod
    def _body_source_text(job: JobPosting) -> str:
        labels = {
            "api-verified": "官方详情 API（正文来源）",
            "detail-probed": "详情探测（正文来源）",
            "list-attested": "列表数据（正文来源，未独立核验）",
            "http-fetched": "HTTP 抓取（正文来源）",
            "historical": "历史正文（当前可用性未知）",
        }
        return labels.get(str(getattr(job, "url_verification", "") or ""), "来源未标注")

    @staticmethod
    def _link_status_text(receipt: Mapping[str, Any]) -> str:
        if not receipt:
            return "未核验（无核验记录）"
        labels = {"verified": "✓ 已核验", "pending": "待核验", "rejected": "未通过"}
        status = str(receipt.get("status") or "").strip().casefold()
        return labels.get(status, "核验状态未知" if not status else f"核验状态未知（{status}）")

    @staticmethod
    def _receipt_text(receipt: Mapping[str, Any], key: str) -> str:
        value = receipt.get(key)
        return str(value).strip() if value is not None and str(value).strip() else "—"

    @staticmethod
    def _matched_text(value: Any) -> str:
        if value is True:
            return "是"
        if value is False:
            return "否"
        return "未知"

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

    @staticmethod
    def _ratio_text(value: Any) -> str:
        if not isinstance(value, Mapping):
            return "未知"
        numerator = value.get("numerator")
        denominator = value.get("denominator")
        ratio = value.get("value")
        if not isinstance(numerator, int) or not isinstance(denominator, int) or denominator <= 0:
            return "未知"
        if not isinstance(ratio, (int, float)):
            return f"{numerator}/{denominator}"
        return f"{numerator}/{denominator} ({ratio:.1%})"

    def _append_execution_trace(
        self,
        lines: list[str],
        execution_trace: list[dict[str, str]] | None,
    ) -> None:
        if not execution_trace:
            return
        lines.extend(["## Agent 执行轨迹", ""])
        for item in execution_trace:
            lines.append(f"- {item.get('node', '-')}: {item.get('summary', '-')}")
        lines.append("")

    def _append_interview_talking_points(
        self,
        lines: list[str],
        orchestration_mode: str,
    ) -> None:
        lines.extend(
            [
                "## 面试讲述要点",
                "",
                "- BrowserClient 抽象隔离 fake browser 和 browser-use session adapter，"
                "保证 demo 可复现，同时保留真实网页接入边界。",
                f"- 编排模式: {orchestration_mode}；同一组节点可用顺序执行或 "
                "LangGraph 编排，便于解释工作流设计而不是一次性脚本。",
                "- Agent 执行轨迹记录 planner、browser、extractor、verifier、"
                "matcher、reporter 节点摘要，方便展示可观测性和问题定位。",
                "- 评测闭环通过 fixture、失败分类、JSON 输出和 Dashboard "
                "证明工作流稳定性，避免只依赖单次 prompt demo。",
                "",
            ]
        )

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
