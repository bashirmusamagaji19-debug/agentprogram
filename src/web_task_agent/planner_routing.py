"""Deterministic planner routing and shared billable-request admission."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4


@dataclass(frozen=True)
class PlannerRoute:
    use_model: bool
    reason: str
    eligible_targets: int


def route_planner(state, action_space, strategy: str) -> PlannerRoute:
    combinations = sum(max(1, len(action_space.targets_by_action.get(action.value, [])))
                       for action in action_space.actions)
    if strategy in {"rules", "policy"}:
        return PlannerRoute(False, "rules_strategy", combinations)
    if action_space.policy_forced:
        return PlannerRoute(False, "deterministic_boundary", combinations)
    if strategy == "always":
        return PlannerRoute(True, "always_eligible", combinations)
    if strategy != "adaptive":
        raise ValueError("unknown planner strategy")
    if combinations <= 1:
        return PlannerRoute(False, "single_legal_choice", combinations)
    urls = action_space.targets_by_action.get("open_page", [])
    summaries = [state.candidate_summaries.get(url, {}) for url in urls]
    semantic = [tuple(str(item.get(key, "")).strip().casefold() for key in ("title", "company", "location"))
                for item in summaries]
    known = [value for value in semantic if any(value)]
    if len(known) >= 2 and len(set(known)) >= 2:
        return PlannerRoute(True, "multiple_grounded_candidates", combinations)
    return PlannerRoute(False, "insufficient_semantic_choice", combinations)


class ModelBudgetExceeded(RuntimeError):
    pass


class ModelCallBudget:
    def __init__(self, *, max_calls: int = 4, max_observed_tokens: int = 100_000,
                 deadline_seconds: float = 180):
        if max_calls < 0 or max_observed_tokens < 0 or deadline_seconds <= 0:
            raise ValueError("invalid model budget")
        self.max_calls = max_calls
        self.max_observed_tokens = max_observed_tokens
        self.deadline = monotonic() + deadline_seconds
        self.calls = 0
        self.usage_missing = 0
        self._known_tokens = 0
        self._known_responses = 0
        self._unknown_reservations = 0
        self._reservations: dict[str, int] = {}
        self._lock = threading.Lock()

    @property
    def observed_tokens(self) -> int | None:
        with self._lock:
            return self._known_tokens if self._known_responses == self.calls else None

    def reserve(self, *, estimated_tokens: int) -> str:
        if estimated_tokens < 0:
            raise ValueError("token reservation cannot be negative")
        with self._lock:
            if (self.calls >= self.max_calls or monotonic() >= self.deadline
                    or self._known_tokens + self._unknown_reservations
                    + sum(self._reservations.values()) + estimated_tokens > self.max_observed_tokens):
                raise ModelBudgetExceeded("model request budget exhausted")
            reservation = uuid4().hex
            self._reservations[reservation] = estimated_tokens
            self.calls += 1
            return reservation

    def finish(self, reservation: str, *, usage: dict | None) -> None:
        with self._lock:
            estimate = self._reservations.pop(reservation)
            total = usage.get("total_tokens") if isinstance(usage, dict) else None
            if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
                self._known_tokens += total
                self._known_responses += 1
            else:
                self.usage_missing += 1
                self._unknown_reservations += estimate
