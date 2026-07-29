from __future__ import annotations

import pytest
from pydantic import ValidationError

from web_task_agent.agent_models import AgentAction, AgentBudget, DecisionAgentState
from web_task_agent.agent_planner import OpenAiCompatibleAgentPlanner
from web_task_agent.models import BrowserPage, UserProfile


def _state() -> DecisionAgentState:
    return DecisionAgentState(
        user=UserProfile(
            keyword="AI agent intern",
            location="Remote",
            skills=["Python", "LangGraph"],
            resume_text="PRIVATE RESUME CONTENT",
        ),
        budget=AgentBudget(max_steps=8, consumed_steps=2),
        candidate_urls=["https://example.com/jobs/1"],
        current_url="https://example.com/jobs/1",
        current_page=BrowserPage(
            url="https://example.com/jobs/1",
            title="AI Engineering Intern",
            content="VERY LONG PRIVATE PAGE BODY " * 100,
        ),
        retry_counts={"https://example.com/jobs/1": 1},
    )


@pytest.mark.asyncio
async def test_openai_compatible_planner_returns_validated_decision():
    requests = []

    def transport(url, headers, payload, timeout_seconds):
        requests.append((url, headers, payload, timeout_seconds))
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"action":"extract_text","reason":"The page is open.",'
                            '"target":"https://example.com/jobs/1",'
                            '"arguments":{},"confidence":0.91}'
                        )
                    }
                }
            ]
        }

    planner = OpenAiCompatibleAgentPlanner(
        provider="deepseek",
        model="deepseek-chat",
        api_key="test-key",
        transport=transport,
    )

    decision = await planner.decide(_state())

    assert decision.action is AgentAction.EXTRACT_TEXT
    assert decision.confidence == 0.91
    assert requests[0][0] == "https://api.deepseek.com/chat/completions"
    assert requests[0][1]["Authorization"] == "Bearer test-key"


@pytest.mark.asyncio
async def test_planner_accepts_json_code_fence():
    def transport(url, headers, payload, timeout_seconds):
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            "```json\n"
                            '{"action":"open_page","reason":"Open best candidate.",'
                            '"target":"https://example.com/jobs/1","confidence":0.8}'
                            "\n```"
                        )
                    }
                }
            ]
        }

    planner = OpenAiCompatibleAgentPlanner(
        provider="qwen",
        model="qwen-plus",
        api_key="test-key",
        transport=transport,
    )

    assert (await planner.decide(_state())).action is AgentAction.OPEN_PAGE


@pytest.mark.asyncio
async def test_planner_rejects_unknown_action():
    def transport(url, headers, payload, timeout_seconds):
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"action":"delete_files","reason":"bad"}'
                    }
                }
            ]
        }

    planner = OpenAiCompatibleAgentPlanner(
        provider="deepseek",
        model="deepseek-chat",
        api_key="test-key",
        transport=transport,
    )

    with pytest.raises(ValidationError):
        await planner.decide(_state())


@pytest.mark.asyncio
async def test_planner_sends_compact_state_without_resume_or_page_body():
    captured = {}

    def transport(url, headers, payload, timeout_seconds):
        captured.update(payload)
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"action":"extract_text","reason":"Page ready."}'
                    }
                }
            ]
        }

    planner = OpenAiCompatibleAgentPlanner(
        provider="deepseek",
        model="deepseek-chat",
        api_key="test-key",
        transport=transport,
    )

    await planner.decide(_state())

    serialized = str(captured)
    assert "PRIVATE RESUME CONTENT" not in serialized
    assert "VERY LONG PRIVATE PAGE BODY" not in serialized
    assert "AI Engineering Intern" in serialized
    assert "remaining_steps" in serialized
    assert "allowed_actions" in serialized

