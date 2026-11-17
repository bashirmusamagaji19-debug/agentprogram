import json

import pytest

from web_task_agent.llm_extractor import OpenAiCompatibleLlmFieldExtractor, OpenAiCompatibleSemanticMatcher
from web_task_agent.models import BrowserPage

FIELDS = {"title": "AI Intern", "company": "Acme", "location": "Remote", "requirements": "Python",
          "responsibilities": "Build agents", "posted_at": "", "skills": ["Python"]}
PAGE = BrowserPage(url="https://example.com/jobs/1", title="AI", content="Python AI intern at Acme")


def response(data, **choice):
    return {"choices": [{"message": {"content": json.dumps(data)}, **choice}]}


@pytest.mark.parametrize("bad", [{**FIELDS, "company": None}, {**FIELDS, "skills": "Python"},
                                  {**FIELDS, "extra": "ignored?"}, {"title": "only title"},
                                  {**FIELDS, "skills": [123]}])
def test_extraction_rejects_invalid_types_extra_and_missing_fields(bad):
    calls = []

    def transport(*args):
        calls.append(args)
        return response(bad)

    extractor = OpenAiCompatibleLlmFieldExtractor(provider="deepseek", model="deepseek-chat",
                                                 api_key="test", transport=transport)
    with pytest.raises(ValueError):
        extractor(PAGE)
    assert len(calls) == 2


def test_invalid_extraction_gets_one_schema_guided_repair():
    requests = []

    def transport(url, headers, payload, timeout):
        requests.append(payload)
        return response({**FIELDS, "skills": "Python"} if len(requests) == 1 else FIELDS)

    extractor = OpenAiCompatibleLlmFieldExtractor(provider="qwen", model="qwen3.7-plus", api_key="test",
                                                 transport=transport)
    assert extractor(PAGE)["skills"] == ["Python"]
    assert len(requests) == 2
    schema = requests[0]["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert "skills" in requests[1]["messages"][-1]["content"]


@pytest.mark.parametrize("choice", [{"finish_reason": "length"}, {"finish_reason": "content_filter"}])
def test_incomplete_or_filtered_response_never_accepted(choice):
    extractor = OpenAiCompatibleLlmFieldExtractor(provider="deepseek", model="deepseek-chat", api_key="test",
                                                 transport=lambda *args: response(FIELDS, **choice))
    with pytest.raises(ValueError):
        extractor(PAGE)


@pytest.mark.parametrize("score", [1.5, -0.1, "0.8", True, float("nan"), float("inf")])
def test_semantic_score_must_be_finite_number_between_zero_and_one(score):
    data = {"score": score, "matched_skills": [], "missing_skills": [], "reason": "match",
            "priority": "high", "suggested_actions": []}
    matcher = OpenAiCompatibleSemanticMatcher(provider="deepseek", model="deepseek-chat", api_key="test",
                                              transport=lambda *args: response(data))
    with pytest.raises(ValueError):
        matcher({})


@pytest.mark.asyncio
async def test_planner_repairs_schema_failure_and_accounts_for_both_requests():
    from web_task_agent.agent_models import DecisionAgentState
    from web_task_agent.agent_planner import OpenAiCompatibleAgentPlanner
    from web_task_agent.models import UserProfile

    requests = []

    def transport(url, headers, payload, timeout):
        requests.append(payload)
        data = {"action": "open_page", "reason": "Open candidate", "arguments": {},
                "target": "https://example.com/jobs/1", "confidence": 0.8}
        if len(requests) == 1:
            data["confidence"] = "0.8"
        return {**response(data), "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}

    planner = OpenAiCompatibleAgentPlanner(provider="deepseek", model="deepseek-chat", api_key="test",
                                           transport=transport)
    state = DecisionAgentState(user=UserProfile(keyword="AI"), candidate_urls=["https://example.com/jobs/1"])
    decision = await planner.decide(state)
    assert decision.confidence == 0.8
    assert len(requests) == planner.budget.calls == planner.telemetry.calls == 2
    assert planner.telemetry.failed_calls == planner.telemetry.successful_calls == 1
    assert planner.telemetry.total_tokens == 40


def test_page_extractor_keeps_rule_result_after_schema_repair_exhausted():
    from web_task_agent.extractor import PageExtractor

    llm = OpenAiCompatibleLlmFieldExtractor(provider="deepseek", model="deepseek-chat", api_key="test",
                                           transport=lambda *args: response({**FIELDS, "company": None}))
    page = BrowserPage(url=PAGE.url, title="AI Intern", content="Hiring AI intern. " * 60)
    baseline = PageExtractor().extract(page)
    result = PageExtractor(llm_field_extractor=llm, llm_min_rule_confidence=1.1).extract(page)
    assert result == baseline
    assert page.metadata["llm_extraction_error"]["category"] == "schema_validation"


def test_reader_rejects_boolean_coercion_and_records_bounded_repair():
    from tests.test_evidence_reader import fixture
    from web_task_agent.evidence_reader import EvidenceJobReader
    from web_task_agent.llm_extractor import LlmProviderConfig

    page, data = fixture()
    data["jobs"][0]["is_ai_role"] = "true"
    reader = EvidenceJobReader(LlmProviderConfig("deepseek", "test", "test", "https://example.com"),
                               transport=lambda *args: response(data))
    result = reader.read(page)
    assert result["status"] == "error"
    assert result["error_category"] == "schema_validation"
    assert result["attempts"] == 2
    assert result["errors"][0]["paths"] == ["jobs.0.is_ai_role"]


@pytest.mark.parametrize("bad_response,category,attempts", [
    ({"choices": []}, "invalid_response", 2),
    ({"choices": [{"message": {"content": " "}}]}, "empty_content", 2),
    ({"choices": [{"message": {"content": json.dumps(FIELDS), "refusal": "declined"}}]}, "refusal", 1),
    ({"choices": [{"message": {"content": '{"title":"A","title":"B"}'}}]}, "invalid_json", 2),
])
def test_response_errors_are_classified_and_repairs_are_bounded(bad_response, category, attempts):
    from web_task_agent.structured_output import StructuredOutputError

    extractor = OpenAiCompatibleLlmFieldExtractor(provider="deepseek", model="test", api_key="test",
                                                 transport=lambda *args: bad_response)
    with pytest.raises(StructuredOutputError) as caught:
        extractor(PAGE)
    assert caught.value.category == category
    assert extractor.last_diagnostics["attempts"] == attempts


def test_truncation_repair_increases_token_budget():
    requests = []

    def transport(url, headers, payload, timeout):
        requests.append(payload)
        return response(FIELDS, finish_reason="length" if len(requests) == 1 else "stop")

    extractor = OpenAiCompatibleLlmFieldExtractor(provider="deepseek", model="test", api_key="test",
                                                 transport=transport)
    assert extractor(PAGE)["title"] == FIELDS["title"]
    assert requests[1]["max_tokens"] > requests[0].get("max_tokens", 0)


@pytest.mark.asyncio
async def test_planner_repair_does_not_bypass_request_budget():
    from web_task_agent.agent_models import DecisionAgentState
    from web_task_agent.agent_planner import OpenAiCompatibleAgentPlanner
    from web_task_agent.models import UserProfile
    from web_task_agent.planner_routing import ModelBudgetExceeded, ModelCallBudget

    requests = []

    def transport(*args):
        requests.append(args)
        return response({"action": "open_page"})

    planner = OpenAiCompatibleAgentPlanner(provider="deepseek", model="test", api_key="test", transport=transport,
                                           budget=ModelCallBudget(max_calls=1))
    with pytest.raises(ModelBudgetExceeded):
        await planner.decide(DecisionAgentState(user=UserProfile(keyword="AI")))
    assert len(requests) == planner.telemetry.calls == planner.budget.calls == 1


@pytest.mark.asyncio
async def test_planner_classifies_invalid_envelope_and_can_repair_it():
    from web_task_agent.agent_models import DecisionAgentState
    from web_task_agent.agent_planner import OpenAiCompatibleAgentPlanner
    from web_task_agent.models import UserProfile

    requests = []

    def transport(*args):
        requests.append(args)
        if len(requests) == 1:
            return None
        return response({"action": "open_page", "reason": "Open candidate", "arguments": {},
                         "target": "https://example.com/jobs/1", "confidence": 0.8})

    planner = OpenAiCompatibleAgentPlanner(provider="deepseek", model="test", api_key="test", transport=transport)
    assert (await planner.decide(DecisionAgentState(user=UserProfile(keyword="AI")))).confidence == 0.8
    assert planner.telemetry.requests[0]["error_category"] == "invalid_response"
