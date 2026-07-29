from __future__ import annotations

from typing import Protocol

from langgraph.graph import END, START, StateGraph

from web_task_agent.agent_models import (
    AgentAction,
    AgentDecision,
    DecisionAgentState,
    DecisionSource,
)
from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.agent_tools import AgentToolRegistry


class AgentPlanner(Protocol):
    async def decide(self, state: DecisionAgentState) -> AgentDecision:
        ...


class HybridAgentRuntime:
    def __init__(
        self,
        *,
        registry: AgentToolRegistry,
        policy: DeterministicAgentPolicy,
        planner: AgentPlanner | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.planner = planner

    async def run(self, state: DecisionAgentState) -> DecisionAgentState:
        result = await self.build_graph().ainvoke(
            state,
            config={"recursion_limit": state.budget.max_steps * 6 + 12},
        )
        if isinstance(result, DecisionAgentState):
            return result
        return DecisionAgentState.model_validate(result)

    def build_graph(self):
        graph = StateGraph(DecisionAgentState)
        graph.add_node("initialize", self._initialize_node)
        graph.add_node("decide", self._decide_node)
        graph.add_node("execute_tool", self._execute_tool_node)
        graph.add_node("observe", self._observe_node)
        graph.add_node("guard", self._guard_node)
        graph.add_node("finish", self._finish_node)
        graph.add_edge(START, "initialize")
        graph.add_edge("initialize", "decide")
        graph.add_edge("decide", "execute_tool")
        graph.add_edge("execute_tool", "observe")
        graph.add_edge("observe", "guard")
        graph.add_conditional_edges(
            "guard",
            self._route_after_guard,
            {"continue": "decide", "finish": "finish"},
        )
        graph.add_edge("finish", END)
        return graph.compile()

    async def _initialize_node(self, state: DecisionAgentState) -> DecisionAgentState:
        return state

    async def _decide_node(self, state: DecisionAgentState) -> DecisionAgentState:
        policy_decision = self.policy.decide(state)
        terminal_reason = policy_decision.arguments.get("terminal_reason")
        if policy_decision.action is AgentAction.FINISH and terminal_reason in {
            "target_reached",
            "budget_exhausted",
            "no_action_available",
        }:
            decision = policy_decision
        elif self.planner is None:
            decision = policy_decision
        else:
            state.metrics.planner_calls += 1
            try:
                raw_decision = await self.planner.decide(state)
                decision = AgentDecision.model_validate(raw_decision).model_copy(
                    update={"source": DecisionSource.LLM}
                )
            except Exception:
                state.metrics.invalid_actions += 1
                state.metrics.fallback_decisions += 1
                decision = policy_decision.model_copy(
                    update={
                        "source": DecisionSource.FALLBACK,
                        "reason": (
                            "Planner output was unavailable or invalid; "
                            f"deterministic fallback selected {policy_decision.action.value}."
                        ),
                    }
                )

        if (
            state.last_observation is not None
            and not state.last_observation.success
            and decision.action is not AgentAction.FINISH
        ):
            state.metrics.recovery_attempts += 1
            state.recovery_in_progress = True

        state.last_decision = decision
        state.decision_history.append(decision)
        return state

    async def _execute_tool_node(self, state: DecisionAgentState) -> DecisionAgentState:
        decision = state.last_decision
        if decision is None:
            raise RuntimeError("execute_tool requires a decision")
        arguments = dict(decision.arguments)
        if decision.target and "url" not in arguments:
            arguments["url"] = decision.target
        executable = decision.model_copy(update={"arguments": arguments})
        state.last_observation = await self.registry.execute(executable, state)
        return state

    async def _observe_node(self, state: DecisionAgentState) -> DecisionAgentState:
        observation = state.last_observation
        decision = state.last_decision
        if observation is None or decision is None:
            raise RuntimeError("observe requires a decision and observation")

        state.observation_history.append(observation)
        state.metrics.tool_calls += 1
        state.metrics.total_latency_ms += observation.latency_ms
        if observation.success:
            state.metrics.successful_tool_calls += 1
            if state.recovery_in_progress:
                state.metrics.successful_recoveries += 1
        state.recovery_in_progress = False

        if decision.action is not AgentAction.FINISH:
            state.budget = state.budget.consume()
        return state

    async def _guard_node(self, state: DecisionAgentState) -> DecisionAgentState:
        return state

    def _route_after_guard(self, state: DecisionAgentState) -> str:
        return "finish" if state.terminal_status != "running" else "continue"

    async def _finish_node(self, state: DecisionAgentState) -> DecisionAgentState:
        return state

