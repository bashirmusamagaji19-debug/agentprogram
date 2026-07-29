from __future__ import annotations

import html
import json
from pathlib import Path
from uuid import uuid4

from web_task_agent.agent_models import DecisionAgentState
from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.agent_runtime import HybridAgentRuntime
from web_task_agent.agent_tools import (
    AgentToolRegistry,
    ExtractTextTool,
    ExtractVisualTool,
    FinishTool,
    OpenPageTool,
    SaveResultsTool,
    ScoreMatchTool,
    SearchJobsTool,
    VerifyJobTool,
)


def build_hybrid_runtime(workflow, *, planner=None) -> HybridAgentRuntime:
    tools = [
        SearchJobsTool(workflow.browser),
        OpenPageTool(workflow.browser),
        ExtractTextTool(workflow.extractor),
        VerifyJobTool(workflow.verifier),
        ScoreMatchTool(workflow.matcher),
        SaveResultsTool(workflow.repository),
        FinishTool(),
    ]
    if workflow.visual_extractor is not None:
        tools.append(ExtractVisualTool(workflow.visual_extractor))
    return HybridAgentRuntime(
        registry=AgentToolRegistry(tools),
        policy=DeterministicAgentPolicy(),
        planner=planner,
    )


def hybrid_state_payload(state: DecisionAgentState) -> dict:
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
            }
        )
    metrics = state.metrics
    return {
        "orchestration_mode": "hybrid-agent",
        "goal": {
            "keyword": state.user.keyword,
            "location": state.user.location,
            "target_count": state.user.target_count,
            "skills": state.user.skills,
        },
        "terminal_status": state.terminal_status,
        "terminal_reason": state.terminal_reason,
        "budget": {
            "max_steps": state.budget.max_steps,
            "consumed_steps": state.budget.consumed_steps,
            "remaining_steps": state.budget.remaining_steps,
        },
        "metrics": {
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
        },
        "candidate_urls": state.candidate_urls,
        "visited_urls": sorted(state.visited_urls),
        "jobs": [job.model_dump(mode="json") for job in state.verified_jobs],
        "matches": [match.model_dump(mode="json") for match in state.matches],
        "trace": trace,
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
) -> dict[str, Path]:
    run_id = f"hybrid-agent-{uuid4().hex[:8]}"
    artifacts: dict[str, Path] = {}

    report_path = Path(report_dir) / f"{run_id}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_hybrid_markdown(state), encoding="utf-8")
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
