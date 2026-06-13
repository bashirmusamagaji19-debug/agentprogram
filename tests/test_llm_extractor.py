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
