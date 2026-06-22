import pytest

from web_task_agent.llm_extractor import (
    LlmExtractorConfigurationError,
    OpenAiCompatibleLlmFieldExtractor,
    build_llm_provider_config,
)
from web_task_agent.models import BrowserPage


def test_deepseek_extractor_posts_openai_compatible_json_request():
    requests = []

    def fake_transport(url, headers, payload, timeout_seconds):
        requests.append(
            {
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout_seconds": timeout_seconds,
            }
        )
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"title":"AI Agent Intern","company":"Example Robotics",'
                            '"location":"Remote","requirements":"Python, LangGraph",'
                            '"responsibilities":"Build browser agents",'
                            '"posted_at":"2026-06-10"}'
                        )
                    }
                }
            ]
        }

    extractor = OpenAiCompatibleLlmFieldExtractor(
        provider="deepseek",
        model="deepseek-v4-flash",
        api_key="test-key",
        transport=fake_transport,
    )

    fields = extractor(
        BrowserPage(
            url="https://example.com/jobs/unstructured",
            title="Careers",
            content="We are hiring an AI Agent Intern at Example Robotics.",
            source="fixture",
        )
    )

    assert fields["title"] == "AI Agent Intern"
    assert fields["company"] == "Example Robotics"
    assert fields["requirements"] == "Python, LangGraph"
    assert requests[0]["url"] == "https://api.deepseek.com/chat/completions"
    assert requests[0]["headers"]["Authorization"] == "Bearer test-key"
    assert requests[0]["payload"]["model"] == "deepseek-v4-flash"
    assert requests[0]["payload"]["response_format"] == {"type": "json_object"}
    assert "Return only valid JSON" in requests[0]["payload"]["messages"][-1]["content"]


def test_qwen_config_uses_dashscope_key_and_openai_compatible_base_url(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-key")

    config = build_llm_provider_config(provider="qwen", model=None)

    assert config.provider == "qwen"
    assert config.model == "qwen-plus"
    assert config.api_key == "qwen-key"
    assert config.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_missing_provider_key_raises_clear_configuration_error(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(LlmExtractorConfigurationError) as exc:
        build_llm_provider_config(provider="deepseek", model=None)

    assert "DEEPSEEK_API_KEY" in str(exc.value)


# ─── DemoLlmMatcher tests ──────────────────────────────────────────────


def test_demo_llm_matcher_computes_skill_overlap():
    from web_task_agent.llm_extractor import DemoLlmMatcher

    matcher = DemoLlmMatcher()
    result = matcher({
        "user_skills": "Python, LangGraph",
        "user_resume": "Built browser agents with FastAPI.",
        "job_title": "AI Agent Intern",
        "job_company": "Example AI",
        "job_requirements": "Python, FastAPI, SQL",
        "job_responsibilities": "Build browser agents",
        "job_skills": "Python, FastAPI, SQL",
    })

    assert result["score"] == 0.67  # 2/3
    assert result["matched_skills"] == ["python", "fastapi"]
    assert result["missing_skills"] == ["sql"]
    assert result["priority"] == "medium"
    assert "语义分析" in result["reason"]


def test_demo_llm_matcher_high_match_when_all_skills_present():
    from web_task_agent.llm_extractor import DemoLlmMatcher

    matcher = DemoLlmMatcher()
    result = matcher({
        "user_skills": "Python, LangGraph, LLM",
        "user_resume": "",
        "job_title": "AI Intern",
        "job_company": "Example",
        "job_requirements": "",
        "job_responsibilities": "",
        "job_skills": "Python, LangGraph",
    })

    assert result["score"] == 1.0
    assert result["priority"] == "high"
    assert result["missing_skills"] == []


def test_demo_llm_matcher_zero_match_when_no_overlap():
    from web_task_agent.llm_extractor import DemoLlmMatcher

    matcher = DemoLlmMatcher()
    result = matcher({
        "user_skills": "Java, Spring",
        "user_resume": "",
        "job_title": "AI Intern",
        "job_company": "Example",
        "job_requirements": "",
        "job_responsibilities": "",
        "job_skills": "Python, LangGraph",
    })

    assert result["score"] == 0.0
    assert result["priority"] == "low"
    assert result["matched_skills"] == []
