from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from pathlib import Path

from web_task_agent.agent_approval import ApprovalStatus
from web_task_agent.agent_components import build_hybrid_runtime as build_hybrid_runtime
from web_task_agent.agent_models import DecisionAgentState


def hybrid_state_payload(state: DecisionAgentState) -> dict:
    from web_task_agent.agent_service import project_hybrid_state

    # Reuse the service projection so CLI exports keep the same action budgets,
    # usage slices and open/latency semantics as the UI and canonical report.
    view = project_hybrid_state(state, run_id=state.execution_id, started_at=datetime.now(UTC))
    details = view.metadata
    trace = []
    for index, decision in enumerate(state.decision_history):
        observation = (
            state.observation_history[index] if index < len(state.observation_history) else None
        )
        trace.append(
            {
                "step": index + 1,
                "action": decision.action.value,
                "source": decision.source.value,
                "reason": decision.reason,
                "target": decision.target,
                "confidence": decision.confidence,
                "observation": (observation.model_dump(mode="json") if observation else None),
                "remaining_budget": details["agent_trace"][index]["remaining_budget"],
                "planner_usage": details["agent_trace"][index]["planner_usage"],
            }
        )
    metrics = state.metrics
    hitl_status = state.terminal_status
    if (
        state.pending_approval is not None
        and state.pending_approval.status is ApprovalStatus.PENDING
    ):
        hitl_status = "awaiting_approval"
    return {
        "run_id": state.execution_id,
        "orchestration_mode": "hybrid-agent",
        "thread_id": state.thread_id or None,
        "hitl_status": hitl_status,
        "pending_approval": (
            state.pending_approval.public_payload() if state.pending_approval is not None else None
        ),
        "approval_audit": [event.model_dump(mode="json") for event in state.approval_audit],
        "goal": {
            "keyword": state.user.keyword,
            "location": state.user.location,
            "target_count": state.user.target_count,
            "skills": state.user.skills,
        },
        "terminal_status": state.terminal_status,
        "terminal_reason": state.terminal_reason,
        "pending_jobs": [job.model_dump(mode="json") for job in view.pending_jobs],
        "budget": {
            "max_steps": state.budget.max_steps,
            "consumed_steps": state.budget.consumed_steps,
            "remaining_steps": state.budget.remaining_steps,
        },
        "metrics": {
            **view.metrics.model_dump(mode="json"),
            "tool_calls": metrics.tool_calls,
            "tool_success_rate": round(metrics.tool_success_rate, 4),
            "recovery_attempts": metrics.recovery_attempts,
            "successful_recoveries": metrics.successful_recoveries,
            "recovery_success_rate": round(metrics.recovery_success_rate, 4),
            "planner_calls": metrics.planner_calls,
            "fallback_decisions": metrics.fallback_decisions,
            "fallback_rate": round(metrics.fallback_rate, 4),
            "invalid_actions": metrics.invalid_actions,
            "average_tool_latency_ms": round(metrics.average_tool_latency_ms, 2),
            "estimated_token_cost": view.metrics.estimated_token_cost,
        },
        "candidate_urls": state.candidate_urls,
        "visited_urls": sorted(state.visited_urls),
        "jobs": [job.model_dump(mode="json") for job in view.jobs],
        "matches": [match.model_dump(mode="json") for match in view.matches],
        "trace": trace,
        "agent_trace": details["agent_trace"],
        "planner_routes": state.planner_routes,
        "model_usage": state.model_usage,
        "model_usage_complete": not state.unmetered_model_roles and all(
            record.get("usage_complete", False) for record in state.model_usage
        ),
        "unmetered_model_roles": state.unmetered_model_roles,
        **{key: details[key] for key in (
            "unique_candidates", "open_successes", "open_failures", "tool_latency_ms",
            "runtime_latency_ms", "approval_wait_ms",
        )},
    }


def render_hybrid_markdown(state: DecisionAgentState) -> str:
    payload = hybrid_state_payload(state)
    metrics = payload["metrics"]
    lines = [
        "# Hybrid Decision Agent Run",
        "",
        f"- Status: {payload['terminal_status']}",
        f"- Terminal reason: {payload['terminal_reason']}",
        f"- Steps: {payload['budget']['consumed_steps']}/{payload['budget']['max_steps']}",
        f"- Tool success rate: {metrics['tool_success_rate']:.2f}",
        f"- Recovery success rate: {metrics['recovery_success_rate']:.2f}",
        f"- Fallback rate: {metrics['fallback_rate']:.2f}",
        "",
        "## Decision And Tool Trace",
        "",
        "| Step | Action | Source | Reason | Observation | Latency ms |",
        "|---:|---|---|---|---|---:|",
    ]
    for item in payload["trace"]:
        observation = item["observation"] or {}
        reason = str(item["reason"]).replace("|", "\\|")
        summary = str(observation.get("summary", "")).replace("|", "\\|")
        lines.append(
            f"| {item['step']} | {item['action']} | {item['source']} | "
            f"{reason} | {summary} | {observation.get('latency_ms', 0):.2f} |"
        )
    if payload["approval_audit"]:
        lines.extend(
            [
                "",
                "## Approval Audit",
                "",
                "| Event | Action | Outcome | Note | UTC time |",
                "|---|---|---|---|---|",
            ]
        )
        for event in payload["approval_audit"]:
            note = str(event.get("note", "")).replace("|", "\\|")
            outcome = event.get("outcome") or ""
            lines.append(
                f"| {event['event']} | {event['action']} | {outcome} | "
                f"{note} | {event['occurred_at']} |"
            )
    return "\n".join(lines) + "\n"


def render_hybrid_html(state: DecisionAgentState) -> str:
    payload = hybrid_state_payload(state)
    rows = []
    for item in payload["trace"]:
        observation = item["observation"] or {}
        rows.append(
            "<tr>"
            f"<td>{item['step']}</td>"
            f"<td><code>{html.escape(str(item['action']))}</code></td>"
            f"<td>{html.escape(str(item['source']))}</td>"
            f"<td>{html.escape(str(item['reason']))}</td>"
            f"<td>{html.escape(str(observation.get('summary', '')))}</td>"
            f"<td>{float(observation.get('latency_ms', 0)):.2f}</td>"
            "</tr>"
        )
    approval_rows = []
    for event in payload["approval_audit"]:
        approval_rows.append(
            "<tr>"
            f"<td>{html.escape(str(event['event']))}</td>"
            f"<td><code>{html.escape(str(event['action']))}</code></td>"
            f"<td>{html.escape(str(event.get('outcome') or ''))}</td>"
            f"<td>{html.escape(str(event.get('note', '')))}</td>"
            f"<td>{html.escape(str(event['occurred_at']))}</td>"
            "</tr>"
        )
    metrics = payload["metrics"]
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Hybrid Decision Agent</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 32px; color: #202124; }}
h1 {{ font-size: 24px; }}
.metrics {{ display: flex; gap: 24px; margin: 20px 0; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ border-bottom: 1px solid #dadce0; padding: 10px; text-align: left; }}
th {{ background: #f8f9fa; }}
code {{ color: #1769aa; }}
</style>
</head>
<body>
<h1>Hybrid Decision Agent</h1>
<p>Status: {html.escape(str(payload["terminal_status"]))} /
{html.escape(str(payload["terminal_reason"]))}</p>
<div class="metrics">
<span>Tool success: {metrics["tool_success_rate"]:.2f}</span>
<span>Recovery success: {metrics["recovery_success_rate"]:.2f}</span>
<span>Fallback: {metrics["fallback_rate"]:.2f}</span>
</div>
<table>
<thead><tr><th>Step</th><th>Action</th><th>Source</th><th>Reason</th><th>Observation</th><th>ms</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
<h2>Approval Audit</h2>
<table>
<thead><tr><th>Event</th><th>Action</th><th>Outcome</th><th>Note</th><th>UTC time</th></tr></thead>
<tbody>{"".join(approval_rows)}</tbody>
</table>
</body>
</html>
"""


def write_hybrid_artifacts(
    state: DecisionAgentState,
    *,
    report_dir: str,
    dashboard_dir: str,
    write_dashboard: bool,
    json_output: str | None,
    run_id: str | None = None,
    report_path: str | Path | None = None,
) -> dict[str, Path]:
    run_id = run_id or state.execution_id
    artifacts: dict[str, Path] = {}

    if report_path is None:
        report_path = Path(report_dir) / f"{run_id}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_hybrid_markdown(state), encoding="utf-8")
    else:
        report_path = Path(report_path)
        if not report_path.is_file():
            raise ValueError("canonical service report is missing")
    artifacts["report"] = report_path

    if write_dashboard:
        dashboard_path = Path(dashboard_dir) / f"{run_id}.html"
        dashboard_path.parent.mkdir(parents=True, exist_ok=True)
        dashboard_path.write_text(render_hybrid_html(state), encoding="utf-8")
        artifacts["dashboard"] = dashboard_path

    if json_output:
        json_path = Path(json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(hybrid_state_payload(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        artifacts["json"] = json_path
    return artifacts
