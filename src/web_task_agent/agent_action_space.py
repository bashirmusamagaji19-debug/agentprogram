"""One source of truth for planner-visible and executable decisions."""
from __future__ import annotations

from pydantic import BaseModel, Field

from web_task_agent.agent_models import AgentAction, AgentDecision, DecisionAgentState


class ActionSpace(BaseModel):
    actions: list[AgentAction]
    targets_by_action: dict[str, list[str]] = Field(default_factory=dict)
    policy_forced: bool = False
    reason: str = ""


def build_action_space(state: DecisionAgentState, policy, *, policy_decision=None) -> ActionSpace:
    decision = policy_decision or policy.decide(state.model_copy(deep=True))
    forced = (decision.action in {AgentAction.FINISH, AgentAction.SAVE_RESULTS, AgentAction.SEARCH_JOBS}
              or state.budget.exhausted
              or (state.last_observation is not None and not state.last_observation.success))
    actions = [decision.action]
    targets = {}
    if decision.target:
        targets[decision.action.value] = [decision.target]
    if decision.action is AgentAction.OPEN_PAGE and not forced:
        allowed = [url for url in state.candidate_urls
                   if url not in state.processed_urls and url not in state.rejected_urls
                   and url not in state.visited_urls and state.retry_counts.get(url, 0) < policy.max_url_attempts]
        targets["open_page"] = allowed[:10]
    if (decision.action is AgentAction.EXTRACT_TEXT and not forced and state.visual_available
            and not state.page_attempts.get(state.current_url or "", {}).get("extract_visual")):
        actions.append(AgentAction.EXTRACT_VISUAL)
        targets["extract_visual"] = [state.current_url] if state.current_url else []
    return ActionSpace(actions=actions, targets_by_action=targets, policy_forced=forced,
                       reason="deterministic_boundary" if forced else "eligible_semantic_choice")


def decision_is_authorized(space: ActionSpace, decision: AgentDecision) -> bool:
    if decision.action not in space.actions:
        return False
    target = decision.target or decision.arguments.get("url")
    if decision.target is not None and "url" in decision.arguments and decision.target != decision.arguments["url"]:
        return False
    allowed = space.targets_by_action.get(decision.action.value, [])
    if allowed:
        return target in allowed
    return target is None
