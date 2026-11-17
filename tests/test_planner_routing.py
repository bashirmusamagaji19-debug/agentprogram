from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from web_task_agent.agent_models import AgentAction, DecisionAgentState
from web_task_agent.agent_planner import OpenAiCompatibleAgentPlanner
from web_task_agent.models import UserProfile


def test_adaptive_only_calls_for_real_semantic_choice():
    from web_task_agent.agent_action_space import build_action_space
    from web_task_agent.agent_policy import DeterministicAgentPolicy
    from web_task_agent.planner_routing import route_planner
    state = DecisionAgentState(user=UserProfile(keyword="AI intern"),
                               candidate_urls=["https://example.com/jobs/1", "https://example.com/jobs/2"])
    space = build_action_space(state, DeterministicAgentPolicy())
    assert not route_planner(state, space, "adaptive").use_model
    state.candidate_summaries = {state.candidate_urls[0]: {"title": "AI Agent Intern"},
                                 state.candidate_urls[1]: {"title": "ML Research Intern"}}
    assert route_planner(state, space, "adaptive").use_model
    assert not route_planner(state, space, "rules").use_model
    assert route_planner(state, space, "always").use_model


def test_global_call_budget_is_atomic_and_unknown_usage_stays_reserved():
    from web_task_agent.planner_routing import ModelBudgetExceeded, ModelCallBudget
    budget = ModelCallBudget(max_calls=1, max_observed_tokens=100)

    def reserve():
        try:
            return budget.reserve(estimated_tokens=80)
        except ModelBudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        reservations = list(executor.map(lambda _: reserve(), range(2)))
    accepted = [value for value in reservations if value is not None]
    assert len(accepted) == 1
    budget.finish(accepted[0], usage=None)
    assert budget.calls == 1
    assert budget.usage_missing == 1
    assert budget.observed_tokens is None


@pytest.mark.asyncio
async def test_planner_transport_does_not_block_async_heartbeat():
    released = threading.Event()

    def transport(url, headers, payload, timeout):
        if not released.wait(0.5):
            raise RuntimeError("event loop was blocked")
        return {"choices": [{"message": {"content": '{"action":"open_page","reason":"open",'
                     '"target":"https://example.com/jobs/1","arguments":{},"confidence":1}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

    planner = OpenAiCompatibleAgentPlanner(provider="qwen", model="fixture", api_key="fixture", transport=transport)
    state = DecisionAgentState(user=UserProfile(keyword="AI intern"), candidate_urls=["https://example.com/jobs/1"])

    async def heartbeat():
        await asyncio.sleep(0.01)
        released.set()

    decision, _ = await asyncio.gather(planner.decide(state), heartbeat())
    assert decision.action is AgentAction.OPEN_PAGE


def test_prompt_only_exposes_current_legal_actions_and_candidate_summaries():
    planner = OpenAiCompatibleAgentPlanner(provider="qwen", model="fixture", api_key="fixture")
    state = DecisionAgentState(user=UserProfile(keyword="AI intern", resume_text="PRIVATE RESUME"),
                               candidate_urls=["https://example.com/jobs/1"],
                               candidate_summaries={"https://example.com/jobs/1": {"title": "AI Agent Intern"}})
    payload = planner._payload(state)
    text = str(payload)
    assert "AI Agent Intern" in text
    assert "save_results" not in text
    assert "PRIVATE RESUME" not in text


def test_mixed_missing_usage_is_unknown_and_keeps_conservative_reservation():
    from web_task_agent.planner_routing import ModelBudgetExceeded, ModelCallBudget

    budget = ModelCallBudget(max_calls=4, max_observed_tokens=100)
    known = budget.reserve(estimated_tokens=30)
    budget.finish(known, usage={"total_tokens": 20})
    missing = budget.reserve(estimated_tokens=70)
    budget.finish(missing, usage=None)
    assert budget.observed_tokens is None
    with pytest.raises(ModelBudgetExceeded):
        budget.reserve(estimated_tokens=11)
    last = budget.reserve(estimated_tokens=10)
    budget.finish(last, usage={"total_tokens": 5})
    assert budget.observed_tokens is None
