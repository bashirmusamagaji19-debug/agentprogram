from __future__ import annotations

import json
import re
from typing import Any

from web_task_agent.agent_models import AgentAction, AgentDecision, DecisionAgentState
from web_task_agent.llm_extractor import (
    PROVIDER_DEFAULTS,
    LlmTransport,
    build_llm_provider_config,
)


def build_configured_agent_planner(
    *,
    provider: str,
    model: str | None = None,
) -> OpenAiCompatibleAgentPlanner:
    config = build_llm_provider_config(provider=provider, model=model)
    return OpenAiCompatibleAgentPlanner(
        provider=config.provider,
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
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
    ) -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or PROVIDER_DEFAULTS[provider]["base_url"]).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or self._urllib_transport

    async def decide(self, state: DecisionAgentState) -> AgentDecision:
        response = self.transport(
            f"{self.base_url}/chat/completions",
            self._headers(),
            self._payload(state),
            self.timeout_seconds,
        )
        content = self._response_content(response)
        return AgentDecision.model_validate(json.loads(self._strip_code_fence(content)))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, state: DecisionAgentState) -> dict[str, Any]:
        summary = {
            "goal": {
                "keyword": state.user.keyword,
                "location": state.user.location,
                "target_count": state.user.target_count,
                "skills": state.user.skills,
            },
            "candidate_urls": state.candidate_urls[:10],
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
            "allowed_actions": [action.value for action in AgentAction],
        }
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Choose exactly one next action for a bounded recruiting Agent. "
                        "Return only a JSON object matching the requested schema. "
                        "Never invent tools outside allowed_actions."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Return JSON with action, reason, optional target, optional arguments, "
                        "and confidence from 0 to 1. State summary:\n"
                        + json.dumps(summary, ensure_ascii=False)
                    ),
                },
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

    @staticmethod
    def _response_content(response: dict[str, Any]) -> str:
        choices = response.get("choices", [])
        if not choices:
            raise ValueError("Planner response did not include choices.")
        content = choices[0].get("message", {}).get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Planner response did not include message content.")
        return content.strip()

    @staticmethod
    def _strip_code_fence(content: str) -> str:
        pattern = r"\x60\x60\x60(?:json)?\s*([\s\S]*?)\s*\x60\x60\x60"
        match = re.fullmatch(pattern, content.strip())
        return match.group(1) if match else content

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
