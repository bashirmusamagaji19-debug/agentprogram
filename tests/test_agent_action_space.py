from __future__ import annotations

import pytest

from web_task_agent.agent_models import AgentAction, AgentDecision, DecisionAgentState
from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.models import UserProfile

from .test_agent_runtime import RecoveringBrowser, _registry


def test_action_space_restricts_actions_and_targets_without_mutation():
    from web_task_agent.agent_action_space import build_action_space
    state = DecisionAgentState(user=UserProfile(keyword="AI intern"),
                               candidate_urls=["https://example.com/jobs/1", "https://example.com/jobs/2"])
    before = state.model_dump()
    space = build_action_space(state, DeterministicAgentPolicy())
    assert space.actions == [AgentAction.OPEN_PAGE]
    assert space.targets_by_action["open_page"] == state.candidate_urls
    assert state.model_dump() == before


@pytest.mark.asyncio
async def test_model_cannot_override_authorized_target_with_arguments_url():
    from web_task_agent.agent_runtime import HybridAgentRuntime
    allowed = "https://example.com/jobs/allowed"
    attacker = "https://attacker.example/jobs/exfiltrate"

    class Planner:
        async def decide(self, state):
            return AgentDecision(action=AgentAction.OPEN_PAGE, reason="open", target=allowed,
                                 arguments={"url": attacker})

    browser = RecoveringBrowser()
    state = DecisionAgentState(user=UserProfile(keyword="AI intern", target_count=1), candidate_urls=[allowed])
    result = await HybridAgentRuntime(registry=_registry(browser), policy=DeterministicAgentPolicy(),
                                      planner=Planner()).run(state)
    assert browser.opened == [allowed]
    assert result.metrics.invalid_actions > 0


@pytest.mark.parametrize("url", ["", None])
@pytest.mark.asyncio
async def test_empty_argument_cannot_override_authorized_target(url):
    from web_task_agent.agent_models import AgentBudget, DecisionSource
    from web_task_agent.agent_runtime import HybridAgentRuntime

    allowed = "https://example.com/jobs/allowed"

    class Planner:
        async def decide(self, state):
            return AgentDecision(action=AgentAction.OPEN_PAGE, reason="open", target=allowed,
                                 arguments={"url": url})

    browser = RecoveringBrowser()
    result = await HybridAgentRuntime(
        registry=_registry(browser), policy=DeterministicAgentPolicy(), planner=Planner(),
    ).run(DecisionAgentState(user=UserProfile(keyword="AI"), candidate_urls=[allowed],
                             budget=AgentBudget(max_steps=1)))
    assert result.decision_history[0].source is DecisionSource.FALLBACK
    assert result.metrics.invalid_actions == 1
    assert result.observation_history[0].success
    assert browser.opened == [allowed]
