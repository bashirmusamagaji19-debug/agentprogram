from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from web_task_agent.agent_action_space import build_action_space, decision_is_authorized
from web_task_agent.agent_approval import (
    ApprovalAuditEvent,
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalStatus,
    HitlRunStatus,
    HitlRuntimeError,
    approval_batch_hash,
)
from web_task_agent.agent_models import (
    AgentAction,
    AgentDecision,
    DecisionAgentState,
    DecisionSource,
    ToolObservation,
)
from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.agent_tools import AgentToolRegistry
from web_task_agent.planner_routing import ModelBudgetExceeded, route_planner


class AgentPlanner(Protocol):
    async def decide(self, state: DecisionAgentState) -> AgentDecision: ...


@dataclass(frozen=True)
class HitlRunResult:
    status: HitlRunStatus
    state: DecisionAgentState
    approval: ApprovalRequest | None = None


@dataclass(frozen=True)
class HitlCheckpointView:
    state: DecisionAgentState
    approval: ApprovalRequest | None
    has_interrupt: bool
    next_nodes: tuple[str, ...]
    pending_execution: bool


class HybridAgentRuntime:
    def __init__(
        self,
        *,
        registry: AgentToolRegistry,
        policy: DeterministicAgentPolicy,
        planner: AgentPlanner | None = None,
        checkpointer=None,
        planner_strategy: str = "always",
        max_planner_calls: int = 4,
        planner_timeout_seconds: float = 20,
        deadline_seconds: float = 180,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.planner = planner
        self.checkpointer = checkpointer
        self.planner_strategy = planner_strategy
        self.max_planner_calls = max_planner_calls
        self.planner_timeout_seconds = planner_timeout_seconds
        self._deadline_seconds = deadline_seconds
        self.deadline = monotonic() + deadline_seconds
        self._planner_disabled = False
        self.last_state: DecisionAgentState | None = None
        self._plain_graph = None
        self._hitl_graph = None
        self._active_sample = monotonic()
        self._tool_attempt_index: int | None = None
        self._completed_observation: ToolObservation | None = None
        self._checkpoint_deadline = self.deadline

    async def run(self, state: DecisionAgentState) -> DecisionAgentState:
        result = await self._invoke_bounded(
            self._graph(hitl=False), state,
            config={"recursion_limit": state.budget.max_steps * 6 + 12}, state=state,
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
        self._checkpoint_deadline = monotonic() + self._deadline_seconds
        thread_id = self._require_thread_id(thread_id)
        graph = self._graph(hitl=True)
        config = self._config(state, thread_id)
        existing = await self._checkpoint_io(graph.aget_state(config))
        if existing.values:
            raise HitlRuntimeError(f"thread {thread_id!r} already exists")
        state.hitl_enabled = True
        state.thread_id = thread_id
        await self._invoke_bounded(graph, state, config=config, state=state)
        return await self._hitl_result(thread_id)

    async def resume_hitl(
        self,
        *,
        thread_id: str,
        decision: ApprovalDecision,
    ) -> HitlRunResult:
        self._checkpoint_deadline = monotonic() + self._deadline_seconds
        thread_id = self._require_thread_id(thread_id)
        graph = self._graph(hitl=True)
        config = self._config(None, thread_id)
        snapshot = await self._checkpoint_io(graph.aget_state(config))
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
        await self._invoke_bounded(
            graph,
            Command(resume=decision.model_dump(mode="json")),
            config=self._config(state, thread_id), state=state,
        )
        return await self._hitl_result(thread_id)

    async def load_hitl_checkpoint(self, thread_id: str) -> HitlCheckpointView:
        """Load the persisted HITL state without executing queued work."""

        self._checkpoint_deadline = monotonic() + self._deadline_seconds
        thread_id = self._require_thread_id(thread_id)
        graph = self._graph(hitl=True)
        snapshot = await self._checkpoint_io(graph.aget_state(self._config(None, thread_id)))
        state = self._state_from_snapshot(snapshot, thread_id)
        request = state.pending_approval
        has_interrupt = self._snapshot_has_interrupt(snapshot)
        pending_execution = bool(
            request is not None
            and request.status in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}
            and not has_interrupt
            and state.terminal_status == "running"
            and bool(snapshot.next)
        )
        return HitlCheckpointView(
            state=state,
            approval=request,
            has_interrupt=has_interrupt,
            next_nodes=tuple(str(node) for node in snapshot.next),
            pending_execution=pending_execution,
        )

    async def recover_hitl(
        self,
        *,
        thread_id: str,
        decision: ApprovalDecision,
    ) -> HitlRunResult:
        """Recover a pending, queued, or already-terminal approval checkpoint."""

        self._checkpoint_deadline = monotonic() + self._deadline_seconds
        thread_id = self._require_thread_id(thread_id)
        graph = self._graph(hitl=True)
        config = self._config(None, thread_id)
        snapshot = await self._checkpoint_io(graph.aget_state(config))
        state = self._state_from_snapshot(snapshot, thread_id)
        request = state.pending_approval
        if request is None:
            raise HitlRuntimeError(f"thread {thread_id!r} has no approval receipt")
        if request.approval_id != decision.approval_id:
            raise HitlRuntimeError("approval_id does not match the pending request")
        if request.payload_hash and request.payload_hash != approval_batch_hash(state.verified_jobs, state.matches):
            raise HitlRuntimeError("approval payload changed after confirmation was requested")

        if request.status is ApprovalStatus.PENDING:
            if not self._snapshot_has_interrupt(snapshot):
                raise HitlRuntimeError("pending approval checkpoint has no interrupt")
            await self._invoke_bounded(
                graph,
                Command(resume=decision.model_dump(mode="json")),
                config=self._config(state, thread_id), state=state,
            )
            return await self._hitl_result(thread_id)

        expected_outcome = (
            ApprovalOutcome.APPROVE if request.status is ApprovalStatus.APPROVED else ApprovalOutcome.REJECT
        )
        if decision.outcome is not expected_outcome:
            raise HitlRuntimeError("approval was already resolved with a different outcome")

        if state.terminal_status != "running":
            return await self._hitl_result(thread_id)
        if not snapshot.next:
            raise HitlRuntimeError("resolved approval checkpoint has no resumable step")
        await self._invoke_bounded(graph, None, config=self._config(state, thread_id), state=state)
        return await self._hitl_result(thread_id)

    async def _invoke_bounded(self, graph, value, *, config, state: DecisionAgentState):
        # Only active execution spends the persisted budget; time awaiting a
        # human response, or spent constructing a runtime, is excluded.
        self.last_state = state
        self._tool_attempt_index = None
        self._completed_observation = None
        self._active_sample = monotonic()
        remaining = max(0.0, self._deadline_seconds - state.metrics.runtime_latency_ms / 1000)
        if state.hitl_enabled:
            remaining = min(remaining, max(0.0, self._checkpoint_deadline - monotonic()))
        self.deadline = self._active_sample + remaining
        timer = asyncio.timeout(remaining)
        try:
            async with timer:
                return await graph.ainvoke(value, config=config)
        except TimeoutError:
            if not timer.expired():
                raise
            terminal = self.deadline_state(state)
            if state.hitl_enabled:
                # Mark the graph terminal without replaying a cancelled tool or
                # leaving an approval interrupt that could still be resumed.
                # Recovery gets a separate bounded I/O grace period. A stuck
                # checkpointer raises rather than claiming durable completion.
                self._checkpoint_deadline = monotonic() + min(5.0, max(1.0, self._deadline_seconds))
                await self._checkpoint_io(graph.aupdate_state(config, terminal, as_node="finish"))
            return terminal

    async def _checkpoint_io(self, operation):
        return await asyncio.wait_for(operation, timeout=max(0.0, self._checkpoint_deadline - monotonic()))

    async def expire_hitl_start(
        self,
        fallback: DecisionAgentState,
        *,
        thread_id: str,
    ) -> HitlRunResult:
        """Persist a truthful deadline terminal checkpoint without tool writes."""

        # Business execution has already stopped. Persisting its terminal state
        # gets bounded I/O grace, independent of a tiny business deadline.
        self._checkpoint_deadline = monotonic() + min(5.0, max(1.0, self._deadline_seconds))
        thread_id = self._require_thread_id(thread_id)
        graph = self._graph(hitl=True)
        config = self._config(None, thread_id)
        snapshot = await self._checkpoint_io(graph.aget_state(config))
        state = self._state_from_snapshot(snapshot, thread_id) if snapshot.values else fallback.model_copy(deep=True)
        state.hitl_enabled = True
        state.thread_id = thread_id
        self.last_state = state
        state = self.deadline_state(state)
        state.pending_approval = None
        self.deadline = monotonic() - 1
        await self._checkpoint_io(graph.aupdate_state(config, state, as_node="finish"))
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
        self.last_state = state
        if monotonic() >= self.deadline:
            decision = AgentDecision(
                action=AgentAction.FINISH,
                reason="Run deadline reached.",
                arguments={"terminal_reason": "deadline_exceeded"},
            )
            state.last_decision = decision
            state.decision_history.append(decision)
            return state
        policy_decision = self.policy.decide(state)
        space = build_action_space(state, self.policy, policy_decision=policy_decision)
        state.allowed_actions = [action.value for action in space.actions]
        state.allowed_targets = space.targets_by_action
        route = route_planner(state, space, self.planner_strategy)
        route_record = asdict(route)
        route_record["step"] = state.budget.consumed_steps
        state.planner_routes.append(route_record)
        if not space.policy_forced:
            state.metrics.planner_opportunities += 1
        budget_exhausted = state.metrics.planner_calls >= self.max_planner_calls or self._planner_disabled
        if self._policy_must_control(state, policy_decision) or self.planner is None or not route.use_model:
            state.metrics.policy_skips += 1
            decision = policy_decision
        elif budget_exhausted:
            state.metrics.budget_skips += 1
            route_record.update(use_model=False, reason="planner_budget_exhausted")
            decision = policy_decision
        else:
            state.metrics.planner_calls += 1
            try:
                raw_decision = await asyncio.wait_for(
                    self.planner.decide(state.model_copy(deep=True)),
                    timeout=min(self.planner_timeout_seconds, max(0.001, self.deadline - monotonic())),
                )
                decision = AgentDecision.model_validate(raw_decision).model_copy(update={"source": DecisionSource.LLM})
                if not decision_is_authorized(space, decision):
                    raise ValueError("planner decision is not authorized for the current state")
            except Exception as error:
                if isinstance(error, TimeoutError):
                    self._planner_disabled = True
                if isinstance(error, ModelBudgetExceeded):
                    # Admission was denied before a request existed. Custom
                    # planners without admission count every decide invocation.
                    state.metrics.planner_calls -= 1
                    state.metrics.budget_skips += 1
                    route_record.update(use_model=False, reason="planner_budget_exhausted")
                    decision = policy_decision
                else:
                    if isinstance(error, ValueError):
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

        telemetry = getattr(self.planner, "telemetry", None)
        if telemetry is not None:
            state.model_usage = [dict(record) for record in getattr(telemetry, "requests", [])]
        previous_calls = sum(len(row["planner_usage"]) for row in state.decision_telemetry)
        state.decision_telemetry.append(
            {
                "remaining_budget": state.budget.remaining_steps,
                "planner_usage": state.model_usage[previous_calls:],
                "planner_calls": state.metrics.planner_calls,
            }
        )
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
        if state.pending_approval is not None and state.pending_approval.status is ApprovalStatus.PENDING:
            return state
        request = ApprovalRequest(
            approval_id=f"approval-{uuid4().hex}",
            thread_id=state.thread_id,
            requested_at=datetime.now(UTC),
            job_count=len(state.verified_jobs),
            summary=f"Persist {len(state.verified_jobs)} verified job records.",
            payload_hash=approval_batch_hash(state.verified_jobs, state.matches),
        )
        state.pending_approval = request
        self._sample_runtime(state)
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
        if request.payload_hash and request.payload_hash != approval_batch_hash(state.verified_jobs, state.matches):
            raise HitlRuntimeError("approval payload changed after confirmation was requested")
        state.metrics.approval_wait_ms += max(0, (datetime.now(UTC) - request.requested_at).total_seconds() * 1000)
        status = ApprovalStatus.APPROVED if decision.outcome is ApprovalOutcome.APPROVE else ApprovalStatus.REJECTED
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
            state.last_decision = state.last_decision.model_copy(update={"arguments": arguments})
            state.decision_history[-1] = state.last_decision
        return state

    @staticmethod
    def _route_after_approval(state: DecisionAgentState) -> str:
        request = state.pending_approval
        return "execute" if request is not None and request.status is ApprovalStatus.APPROVED else "deny"

    async def _human_denied_node(
        self,
        state: DecisionAgentState,
    ) -> DecisionAgentState:
        state.terminal_status = "rejected"
        state.terminal_reason = "human_denied"
        self._record_observed_budget(state, len(state.decision_history) - 1)
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
        return decision_is_authorized(
            build_action_space(state, self.policy, policy_decision=policy_decision),
            decision,
        )

    async def _execute_tool_node(self, state: DecisionAgentState) -> DecisionAgentState:
        self.last_state = state
        self._tool_attempt_index = len(state.observation_history)
        self._completed_observation = None
        decision = state.last_decision
        if decision is None:
            raise RuntimeError("execute_tool requires a decision")
        arguments = dict(decision.arguments)
        if decision.target and "url" not in arguments:
            arguments["url"] = decision.target
        executable = decision.model_copy(update={"arguments": arguments})
        state.last_observation = await self.registry.execute(executable, state)
        self._completed_observation = state.last_observation
        return state

    async def _observe_node(self, state: DecisionAgentState) -> DecisionAgentState:
        self._sample_runtime(state)
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
        self._record_observed_budget(state, len(state.observation_history) - 1)
        self._tool_attempt_index = None
        self._completed_observation = None
        self.last_state = state
        return state

    def deadline_state(self, fallback: DecisionAgentState) -> DecisionAgentState:
        state = self.last_state or fallback
        if state.terminal_reason == "deadline_exceeded" and state.terminal_status != "running":
            return state
        self._sample_runtime(state)
        telemetry = getattr(self.planner, "telemetry", None)
        if telemetry is not None:
            state.model_usage = [dict(record) for record in getattr(telemetry, "requests", [])]
        while len(state.observation_history) < len(state.decision_history):
            index = len(state.observation_history)
            decision = state.decision_history[index]
            attempted = index == self._tool_attempt_index
            observation = self._completed_observation if attempted else None
            if observation is None:
                observation = ToolObservation(
                    tool_name=decision.action,
                    success=False,
                    summary="Run deadline exceeded.",
                    error_category="deadline_exceeded",
                    error_message="deadline_exceeded",
                )
            state.observation_history.append(observation)
            state.metrics.total_latency_ms += observation.latency_ms
            # Interrupted non-finish attempts spend one step, just as completed
            # attempts do. Repeated deadline projection must not spend it twice.
            if attempted and decision.action is not AgentAction.FINISH:
                state.budget = state.budget.consume()
            self._record_observed_budget(state, len(state.observation_history) - 1)
        decision = AgentDecision(
            action=AgentAction.FINISH,
            reason="Run deadline exceeded.",
            arguments={"terminal_reason": "deadline_exceeded"},
        )
        state.terminal_status = "partial" if state.verified_jobs and state.saved else "failed"
        state.terminal_reason = "deadline_exceeded"
        if state.pending_approval is not None and state.pending_approval.status is ApprovalStatus.PENDING:
            state.pending_approval = None
        state.last_decision = decision
        state.decision_history.append(decision)
        observation = ToolObservation(
            tool_name=AgentAction.FINISH, success=True, summary="Stopped at the run deadline."
        )
        state.observation_history.append(observation)
        state.last_observation = observation
        self._record_observed_budget(state, len(state.observation_history) - 1)
        assigned_usage = sum(len(row["planner_usage"]) for row in state.decision_telemetry)
        state.decision_telemetry[-1]["planner_usage"].extend(state.model_usage[assigned_usage:])
        state.metrics.tool_calls = len(state.observation_history)
        state.metrics.successful_tool_calls = sum(item.success for item in state.observation_history)
        self._tool_attempt_index = None
        self._completed_observation = None
        return state

    @staticmethod
    def _record_observed_budget(state: DecisionAgentState, index: int) -> None:
        while len(state.decision_telemetry) <= index:
            state.decision_telemetry.append({
                "remaining_budget": state.budget.remaining_steps,
                "planner_usage": [],
                "planner_calls": state.metrics.planner_calls,
            })
        state.decision_telemetry[index]["observed_remaining_budget"] = state.budget.remaining_steps

    async def _guard_node(self, state: DecisionAgentState) -> DecisionAgentState:
        return state

    def _route_after_guard(self, state: DecisionAgentState) -> str:
        return "finish" if state.terminal_status != "running" else "continue"

    async def _finish_node(self, state: DecisionAgentState) -> DecisionAgentState:
        self._sample_runtime(state)
        return state

    def _sample_runtime(self, state: DecisionAgentState) -> None:
        now = monotonic()
        state.metrics.runtime_latency_ms += max(0, now - self._active_sample) * 1000
        self._active_sample = now

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
        snapshot = await self._checkpoint_io(self._graph(hitl=True).aget_state(self._config(None, thread_id)))
        state = self._state_from_snapshot(snapshot, thread_id)
        request = state.pending_approval
        if request is not None and request.status is ApprovalStatus.PENDING and self._snapshot_has_interrupt(snapshot):
            status = HitlRunStatus.AWAITING_APPROVAL
        else:
            try:
                status = HitlRunStatus(state.terminal_status)
            except ValueError as exc:
                raise HitlRuntimeError(
                    f"thread {thread_id!r} stopped in invalid status {state.terminal_status!r}"
                ) from exc
        return HitlRunResult(status=status, state=state, approval=request)
