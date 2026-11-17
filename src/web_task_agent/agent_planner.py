from __future__ import annotations

import asyncio
import hashlib
import json
from time import monotonic, perf_counter
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from web_task_agent.agent_action_space import build_action_space
from web_task_agent.agent_models import AgentDecision, DecisionAgentState
from web_task_agent.agent_policy import DeterministicAgentPolicy
from web_task_agent.llm_extractor import (
    PROVIDER_DEFAULTS,
    LlmTransport,
    build_llm_provider_config,
)
from web_task_agent.planner_routing import ModelCallBudget
from web_task_agent.structured_output import (
    PlannerOutput,
    StructuredOutputError,
    constrain_payload,
    parse_output,
    repair_payload,
)


class PlannerTelemetry(BaseModel):
    calls: int = Field(default=0, ge=0)
    successful_calls: int = Field(default=0, ge=0)
    failed_calls: int = Field(default=0, ge=0)
    total_latency_ms: float = Field(default=0.0, ge=0.0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    usage_missing: int = Field(default=0, ge=0)
    requests: list[dict[str, Any]] = Field(default_factory=list)


def build_configured_agent_planner(
    *,
    provider: str,
    model: str | None = None,
    budget: ModelCallBudget | None = None,
    timeout_seconds: int = 20,
) -> OpenAiCompatibleAgentPlanner:
    config = build_llm_provider_config(provider=provider, model=model)
    return OpenAiCompatibleAgentPlanner(
        provider=config.provider,
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        budget=budget,
        timeout_seconds=timeout_seconds,
    )


class OpenAiCompatibleAgentPlanner:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        base_url: str | None = None,
        timeout_seconds: int = 60,
        transport: LlmTransport | None = None,
        budget: ModelCallBudget | None = None,
        schema_mode: str = "auto",
    ) -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or PROVIDER_DEFAULTS[provider]["base_url"]).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or self._urllib_transport
        self.telemetry = PlannerTelemetry()
        self.budget = budget or ModelCallBudget()
        self.schema_mode = schema_mode

    async def decide(self, state: DecisionAgentState) -> AgentDecision:
        payload = constrain_payload(self._payload(state), PlannerOutput, provider=self.provider, mode=self.schema_mode)
        deadline = min(monotonic() + self.timeout_seconds, self.budget.deadline)
        for attempt in range(2):
            try:
                return await self._decide_once(payload, deadline=deadline, repair_attempt=attempt)
            except StructuredOutputError as exc:
                if attempt or not exc.repairable:
                    raise
                payload = repair_payload(payload, exc)
        raise AssertionError("unreachable")

    async def _decide_once(self, payload: dict[str, Any], *, deadline: float, repair_attempt: int) -> AgentDecision:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        reservation = self.budget.reserve(estimated_tokens=len(encoded) + payload["max_tokens"])
        self.telemetry.calls += 1
        started = perf_counter()
        usage = None
        record = {"request_id": uuid4().hex, "provider": self.provider, "model": self.model,
                  "prompt_hash": hashlib.sha256(encoded).hexdigest(),
                  "input_hash": hashlib.sha256(payload["messages"][1]["content"].encode()).hexdigest(),
                  "usage_complete": False, "prompt_tokens": None, "completion_tokens": None,
                  "total_tokens": None, "status": "error"}
        record.update(repair_attempt=repair_attempt, output_mode=payload["response_format"]["type"])
        try:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("planner deadline exceeded")
            response = await asyncio.wait_for(asyncio.to_thread(self.transport,
                f"{self.base_url}/chat/completions",
                self._headers(),
                payload,
                remaining,
            ), timeout=remaining)
            usage = response.get("usage") if isinstance(response, dict) else None
            if isinstance(usage, dict):
                self._record_usage(response)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    value = usage.get(key)
                    record[key] = (
                        value
                        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                        else None
                    )
                record["usage_complete"] = all(record[key] is not None
                                                for key in ("prompt_tokens", "completion_tokens", "total_tokens"))
            record["response_model"] = response.get("model", self.model) if isinstance(response, dict) else self.model
            output = parse_output(response, PlannerOutput)
            decision = AgentDecision.model_validate(output.model_dump())
        except (Exception, asyncio.CancelledError) as error:
            self.telemetry.failed_calls += 1
            record["error_type"] = type(error).__name__
            if isinstance(error, StructuredOutputError):
                record["error_category"] = error.category
                record["error_paths"] = error.paths
            record["timeout"] = isinstance(error, (TimeoutError, asyncio.CancelledError))
            raise
        else:
            self.telemetry.successful_calls += 1
            record["status"] = "ok"
            return decision
        finally:
            record["elapsed_ms"] = (perf_counter() - started) * 1000
            self.telemetry.total_latency_ms += record["elapsed_ms"]
            if not record["usage_complete"]:
                self.telemetry.usage_missing += 1
            self.telemetry.requests.append(record)
            self.budget.finish(reservation, usage=usage)

    def _record_usage(self, response: dict[str, Any]) -> None:
        usage = response.get("usage", {})
        if not isinstance(usage, dict):
            return
        self.telemetry.prompt_tokens += self._non_negative_int(usage.get("prompt_tokens"))
        self.telemetry.completion_tokens += self._non_negative_int(usage.get("completion_tokens"))
        self.telemetry.total_tokens += self._non_negative_int(usage.get("total_tokens"))

    @staticmethod
    def _non_negative_int(value: Any) -> int:
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return 0

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, state: DecisionAgentState) -> dict[str, Any]:
        space = build_action_space(state, DeterministicAgentPolicy())
        allowed_actions = state.allowed_actions or [action.value for action in space.actions]
        allowed_targets = state.allowed_targets or space.targets_by_action
        visible_urls = list(dict.fromkeys(url for targets in allowed_targets.values() for url in targets))[:10]
        summary = {
            "goal": {
                "keyword": state.user.keyword,
                "location": state.user.location,
                "target_count": state.user.target_count,
                "skills": state.user.skills,
            },
            "candidate_urls": visible_urls,
            "candidate_summaries": {url: state.candidate_summaries.get(url, {}) for url in visible_urls},
            "allowed_targets": allowed_targets,
            "visited_urls": sorted(state.visited_urls)[:10],
            "current_page": (
                {
                    "url": state.current_page.url,
                    "title": state.current_page.title,
                    "source": state.current_page.source,
                }
                if state.current_page
                else None
            ),
            "last_observation": (
                {
                    "tool_name": state.last_observation.tool_name.value,
                    "success": state.last_observation.success,
                    "summary": state.last_observation.summary[:500],
                    "error_category": state.last_observation.error_category,
                    "recoverable": state.last_observation.recoverable,
                }
                if state.last_observation
                else None
            ),
            "retry_counts": state.retry_counts,
            "remaining_steps": state.budget.remaining_steps,
            "allowed_actions": allowed_actions,
        }
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Choose exactly one next action for a bounded recruiting Agent. "
                        "Return only a JSON object matching the requested schema. "
                        "Never invent tools outside allowed_actions. When allowed_targets lists targets for the "
                        "action, target must be exactly one listed URL. Never override target with arguments.url. "
                        "Select candidates using the goal and source-grounded summaries, not invented page content."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Return JSON with action, reason, target (URL or null), arguments (empty object), "
                        "and confidence from 0 to 1. State summary:\n"
                        + json.dumps(summary, ensure_ascii=False)
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": 600,
            "response_format": {"type": "json_object"},
        }

    @staticmethod
    def _urllib_transport(
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        from urllib import request

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method="POST")
        with request.urlopen(req, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
