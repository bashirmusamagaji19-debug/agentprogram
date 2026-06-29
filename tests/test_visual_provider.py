"""Tests for the real visual provider bridge."""

import pytest

from web_task_agent.models import BrowserPage
from web_task_agent.visual_provider import (
    VisualProviderConfigurationError,
    build_configured_visual_extractor,
)


@pytest.mark.asyncio
async def test_visual_provider_adapter_converts_external_result_to_visual_fields():
    """External visual-web-agent result is mapped into Agent's VisualJobFields."""

    class FakeExternalJob:
        title = "Real Visual AI Intern"
        company = "Example Vision"
        location = "Remote"
        requirements = "Python, Playwright, Qwen-VL"
        responsibilities = "Extract fields from screenshots"
        skills = ["Python", "Playwright", "Qwen-VL"]
        confidence = 0.91

    class FakeExternalExtractor:
        async def extract(self, url: str):

            class Result:
                success = True
                job = FakeExternalJob()
                error = ""

            return Result()

    adapter = build_configured_visual_extractor(
        provider="qwen-vl",
        extractor_factory=lambda: FakeExternalExtractor(),
    )

    result = await adapter.extract(
        BrowserPage(
            url="https://example.com/jobs/visual",
            title="",
            content="",
            source="demo",
        )
    )

    assert result.success is True
    assert result.fields is not None
    assert result.fields.title == "Real Visual AI Intern"
    assert result.fields.company == "Example Vision"
    assert result.fields.skills == ["Python", "Playwright", "Qwen-VL"]


def test_visual_provider_builder_raises_clear_error_when_dependency_is_missing(
    monkeypatch,
):
    """Missing visual-web-agent package produces a clear install instruction."""

    def fake_import(name):
        raise ModuleNotFoundError("No module named 'visual_web_agent'")

    monkeypatch.setattr(
        "web_task_agent.visual_provider.import_module", fake_import
    )

    with pytest.raises(VisualProviderConfigurationError) as exc:
        build_configured_visual_extractor(provider="qwen-vl")

    assert "pip install -e" in str(exc.value)


def test_visual_provider_builder_raises_on_unsupported_provider():
    """Unknown provider names raise immediately."""
    with pytest.raises(VisualProviderConfigurationError) as exc:
        build_configured_visual_extractor(provider="unknown-vl")

    assert "Unsupported visual provider" in str(exc.value)


def test_qwen_adapter_has_uses_own_browser():
    """Real providers signal that they fetch pages on their own."""
    adapter = build_configured_visual_extractor(
        provider="qwen-vl",
        extractor_factory=lambda: object(),  # won't be called for extract()
    )
    assert adapter.uses_own_browser is True
    assert adapter.provider == "qwen-vl"
