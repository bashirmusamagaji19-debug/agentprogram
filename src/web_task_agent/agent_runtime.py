from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from web_task_agent.agent_approval import (
    ApprovalAuditEvent,
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalStatus,
    HitlRunStatus,
    HitlRuntimeError,
)
from web_task_agent.agent_models import (
    AgentAction,
    AgentDecision,
    DecisionAgentState,
    DecisionSource,
)
from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.agent_tools import AgentToolRegistry


class AgentPlanner(Protocol):
    async def decide(self, state: DecisionAgentState) -> AgentDecision: ...


@dataclass(frozen=True)
class HitlRunResult:
    status: HitlRunStatus
    state: DecisionAgentState
    approval: ApprovalRequest | None = None


class HybridAgentRuntime:
    def __init__(
        self,
        *,
        registry: AgentToolRegistry,
        policy: DeterministicAgentPolicy,
        planner: AgentPlanner | None = None,
        checkpointer=None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.planner = planner
        self.checkpointer = checkpointer
        self._plain_graph = None
        self._hitl_graph = None

    async def run(self, state: DecisionAgentState) -> DecisionAgentState:
        result = await self._graph(hitl=False).ainvoke(
            state,
            config={"recursion_limit": state.budget.max_steps * 6 + 12},
        )
        if isinstance(result, DecisionAgentState):
            return result
        return DecisionAgentState.model_validate(result)

    async def start_hitl(
        self,
        state: DecisionAgentState,
        *,
        thread_id: str,
    ) -> HitlRunResult:
        thread_id = self._require_thread_id(thread_id)
        graph = self._graph(hitl=True)
        config = self._config(state, thread_id)
        existing = await graph.aget_state(config)
        if existing.values:
            raise HitlRuntimeError(f"thread {thread_id!r} already exists")
        state.hitl_enabled = True
        state.thread_id = thread_id
        await graph.ainvoke(state, config=config)
        return await self._hitl_result(thread_id)

    async def resume_hitl(
        self,
        *,
        thread_id: str,
        decision: ApprovalDecision,
    ) -> HitlRunResult:
        thread_id = self._require_thread_id(thread_id)
        graph = self._graph(hitl=True)
        config = self._config(None, thread_id)
        snapshot = await graph.aget_state(config)
        state = self._state_from_snapshot(snapshot, thread_id)
        request = state.pending_approval
        if (
            request is None
            or request.status is not ApprovalStatus.PENDING
            or not self._snapshot_has_interrupt(snapshot)
        ):
            raise HitlRuntimeError(f"thread {thread_id!r} has no pending approval")
        if request.approval_id != decision.approval_id:
            raise HitlRuntimeError("approval_id does not match the pending request")
        await graph.ainvoke(
            Command(resume=decision.model_dump(mode="json")),
            config=self._config(state, thread_id),
        )
        return await self._hitl_result(thread_id)

    def build_graph(self):
        return self._graph(hitl=False)

    def _graph(self, *, hitl: bool):
        if hitl:
            if self.checkpointer is None:
                raise HitlRuntimeError("HITL runtime requires a checkpointer")
            if self._hitl_graph is None:
                self._hitl_graph = self._compile_graph(checkpointer=self.checkpointer)
            return self._hitl_graph
        if self._plain_graph is None:
            self._plain_graph = self._compile_graph(checkpointer=None)
        return self._plain_graph

    def _compile_graph(self, *, checkpointer):
        graph = StateGraph(DecisionAgentState)
        graph.add_node("initialize", self._initialize_node)
        graph.add_node("decide", self._decide_node)
        graph.add_node("prepare_approval", self._prepare_approval_node)
        graph.add_node("approval_gate", self._approval_gate_node)
        graph.add_node("human_denied", self._human_denied_node)
        graph.add_node("execute_tool", self._execute_tool_node)
        graph.add_node("observe", self._observe_node)
        graph.add_node("guard", self._guard_node)
        graph.add_node("finish", self._finish_node)
        graph.add_edge(START, "initialize")
        graph.add_edge("initialize", "decide")
        graph.add_conditional_edges(
            "decide",
            self._route_after_decision,
            {"execute": "execute_tool", "approve": "prepare_approval"},
        )
        graph.add_edge("prepare_approval", "approval_gate")
        graph.add_conditional_edges(
            "approval_gate",
            self._route_after_approval,
            {"execute": "execute_tool", "deny": "human_denied"},
        )
        graph.add_edge("human_denied", "finish")
        graph.add_edge("execute_tool", "observe")
        graph.add_edge("observe", "guard")
        graph.add_conditional_edges(
            "guard",
            self._route_after_guard,
            {"continue": "decide", "finish": "finish"},
        )
        graph.add_edge("finish", END)
        return graph.compile(checkpointer=checkpointer)

    async def _initialize_node(self, state: DecisionAgentState) -> DecisionAgentState:
        return state

    async def _decide_node(self, state: DecisionAgentState) -> DecisionAgentState:
        policy_decision = self.policy.decide(state)
        if self._policy_must_control(state, policy_decision):
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
                if not self._planner_decision_is_authorized(
                    state,
                    decision,
                    policy_decision,
                ):
                    raise ValueError("planner decision is not authorized for the current state")
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

    def _route_after_decision(self, state: DecisionAgentState) -> str:
        return (
            "approve"
            if state.hitl_enabled
            and state.last_decision is not None
            and state.last_decision.action is AgentAction.SAVE_RESULTS
            else "execute"
        )

    async def _prepare_approval_node(
        self,
        state: DecisionAgentState,
    ) -> DecisionAgentState:
        if (
            state.pending_approval is not None
            and state.pending_approval.status is ApprovalStatus.PENDING
        ):
            return state
        request = ApprovalRequest(
            approval_id=f"approval-{uuid4().hex}",
            thread_id=state.thread_id,
            requested_at=datetime.now(UTC),
            job_count=len(state.verified_jobs),
            summary=f"Persist {len(state.verified_jobs)} verified job records.",
        )
        state.pending_approval = request
        state.approval_audit.append(
            ApprovalAuditEvent(
                approval_id=request.approval_id,
                event="requested",
                occurred_at=request.requested_at,
            )
        )
        return state

    async def _approval_gate_node(
        self,
        state: DecisionAgentState,
    ) -> DecisionAgentState:
        request = state.pending_approval
        if request is None or request.status is not ApprovalStatus.PENDING:
            raise HitlRuntimeError("approval gate requires a pending request")
        raw_decision = interrupt(request.public_payload())
        decision = ApprovalDecision.model_validate(raw_decision)
        if decision.approval_id != request.approval_id:
            raise HitlRuntimeError("approval_id does not match the pending request")
        status = (
            ApprovalStatus.APPROVED
            if decision.outcome is ApprovalOutcome.APPROVE
            else ApprovalStatus.REJECTED
        )
        state.pending_approval = request.model_copy(update={"status": status})
        state.approval_audit.append(
            ApprovalAuditEvent(
                approval_id=request.approval_id,
                event="resolved",
                occurred_at=datetime.now(UTC),
                outcome=decision.outcome,
                note=decision.note,
            )
        )
        if decision.outcome is ApprovalOutcome.APPROVE:
            if state.last_decision is None:
                raise HitlRuntimeError("approval gate requires the original decision")
            arguments = dict(state.last_decision.arguments)
            arguments["approval_id"] = request.approval_id
            state.last_decision = state.last_decision.model_copy(
                update={"arguments": arguments}
            )
            state.decision_history[-1] = state.last_decision
        return state

    @staticmethod
    def _route_after_approval(state: DecisionAgentState) -> str:
        request = state.pending_approval
        return (
            "execute"
            if request is not None and request.status is ApprovalStatus.APPROVED
            else "deny"
        )

    async def _human_denied_node(
        self,
        state: DecisionAgentState,
    ) -> DecisionAgentState:
        state.terminal_status = "rejected"
        state.terminal_reason = "human_denied"
        return state

    def _policy_must_control(
        self,
        state: DecisionAgentState,
        policy_decision: AgentDecision,
    ) -> bool:
        terminal_reason = policy_decision.arguments.get("terminal_reason")
        if policy_decision.action is AgentAction.FINISH and terminal_reason in {
            "target_reached",
            "budget_exhausted",
            "no_action_available",
        }:
            return True
        if state.last_observation is not None and not state.last_observation.success:
            return True
        return (
            policy_decision.action is AgentAction.EXTRACT_VISUAL
            and state.last_observation is not None
            and state.last_observation.tool_name is AgentAction.EXTRACT_TEXT
        )

    def _planner_decision_is_authorized(
        self,
        state: DecisionAgentState,
        decision: AgentDecision,
        policy_decision: AgentDecision,
    ) -> bool:
        if decision.action is AgentAction.FINISH:
            return False

        if decision.action is AgentAction.OPEN_PAGE:
            target = decision.target or decision.arguments.get("url")
            return bool(
                target
                and target in state.candidate_urls
                and state.retry_counts.get(target, 0) < self.policy.max_url_attempts
            )

        if decision.action in {AgentAction.EXTRACT_TEXT, AgentAction.EXTRACT_VISUAL}:
            if state.current_page is None:
                return False
            if decision.action is AgentAction.EXTRACT_VISUAL and not state.visual_available:
                return False
            target = decision.target or decision.arguments.get("url")
            if target and target != state.current_page.url:
                return False
            return policy_decision.action in {
                AgentAction.EXTRACT_TEXT,
                AgentAction.EXTRACT_VISUAL,
            }

        if decision.action is not policy_decision.action:
            return False
        return not (
            policy_decision.target
            and decision.target
            and decision.target != policy_decision.target
        )

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

    @staticmethod
    def _require_thread_id(thread_id: str) -> str:
        thread_id = thread_id.strip()
        if not thread_id:
            raise HitlRuntimeError("thread_id must not be blank")
        return thread_id

    @staticmethod
    def _config(
        state: DecisionAgentState | None,
        thread_id: str,
    ) -> dict:
        recursion_limit = state.budget.max_steps * 6 + 12 if state is not None else 100
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": recursion_limit,
        }

    @staticmethod
    def _state_from_snapshot(snapshot, thread_id: str) -> DecisionAgentState:
        if not snapshot.values:
            raise HitlRuntimeError(f"thread {thread_id!r} was not found")
        if isinstance(snapshot.values, DecisionAgentState):
            return snapshot.values
        return DecisionAgentState.model_validate(snapshot.values)

    @staticmethod
    def _snapshot_has_interrupt(snapshot) -> bool:
        return any(getattr(task, "interrupts", ()) for task in snapshot.tasks)

    async def _hitl_result(self, thread_id: str) -> HitlRunResult:
        snapshot = await self._graph(hitl=True).aget_state(
            self._config(None, thread_id)
        )
        state = self._state_from_snapshot(snapshot, thread_id)
        request = state.pending_approval
        if (
            request is not None
            and request.status is ApprovalStatus.PENDING
            and self._snapshot_has_interrupt(snapshot)
        ):
            status = HitlRunStatus.AWAITING_APPROVAL
        else:
            try:
                status = HitlRunStatus(state.terminal_status)
            except ValueError as exc:
                raise HitlRuntimeError(
                    f"thread {thread_id!r} stopped in invalid status "
                    f"{state.terminal_status!r}"
                ) from exc
        return HitlRunResult(status=status, state=state, approval=request)
