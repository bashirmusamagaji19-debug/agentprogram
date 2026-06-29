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
    # API key check must pass before the import is attempted
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

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


@pytest.mark.asyncio
async def test_qwen_adapter_close_delegates_to_extractor():
    """Adapter.close() calls the underlying extractor's close()."""
    closed = False

    class CloseableExtractor:
        async def extract(self, url: str):
            raise AssertionError("should not be called")

        async def close(self):
            nonlocal closed
            closed = True

    adapter = build_configured_visual_extractor(
        provider="qwen-vl",
        extractor_factory=lambda: CloseableExtractor(),
    )

    await adapter.close()

    assert closed is True


def test_build_configured_visual_extractor_checks_api_key(monkeypatch):
    """Missing DASHSCOPE_API_KEY raises VisualProviderConfigurationError
    before trying to create the VLM client."""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    with pytest.raises(VisualProviderConfigurationError) as exc:
        build_configured_visual_extractor(provider="qwen-vl")

    assert "DASHSCOPE_API_KEY" in str(exc.value)


@pytest.mark.asyncio
async def test_adapter_rejects_empty_vlm_fields_as_quality_failure():
    """VLM call that returns empty/placeholder fields is not counted as success."""

    class EmptyExternalJob:
        title = ""
        company = ""
        location = ""
        requirements = ""
        responsibilities = ""
        skills = []
        confidence = 0.0

    class EmptyExternalExtractor:
        async def extract(self, url: str):

            class Result:
                success = True
                job = EmptyExternalJob()
                error = ""

            return Result()

    adapter = build_configured_visual_extractor(
        provider="qwen-vl",
        extractor_factory=lambda: EmptyExternalExtractor(),
    )

    result = await adapter.extract(
        BrowserPage(url="https://example.com/jobs/garbage", title="", content="", source="demo")
    )

    assert result.success is False
    assert result.fields is None
    assert "placeholder fields" in result.error


def test_cli_visual_provider_returns_exit_1_when_no_valid_jobs(
    tmp_path, monkeypatch, capsys
) -> None:
    """--visual-extractor-provider with empty results returns exit code 1."""
    monkeypatch.chdir(tmp_path)

    class EmptyAdapter:
        provider = "qwen-vl"
        model = "qwen-vl-plus"
        uses_own_browser = True

        async def extract(self, page):
            from web_task_agent.visual_extractor import VisualExtractionResult

            return VisualExtractionResult(
                url=page.url,
                success=False,
                fields=None,
                error="no content on page",
            )

        async def close(self):
            pass

    monkeypatch.setattr(
        "web_task_agent.cli.build_configured_visual_extractor",
        lambda *, provider, model=None: EmptyAdapter(),
    )

    # Import main locally to avoid module-level side effects
    from web_task_agent.cli import main

    exit_code = main(
        [
            "--seed-url",
            "https://example.com/jobs/visual-ai-intern",
            "--target-count",
            "1",
            "--visual-extractor-provider",
            "qwen-vl",
            "--json-output",
            "outputs/provider-empty.json",
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Valid jobs: 0" in captured.out
