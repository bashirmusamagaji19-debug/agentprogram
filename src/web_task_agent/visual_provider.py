"""Bridge from ``visual-web-agent`` to ``web_task_agent`` visual extraction.

This module provides a runtime adapter that wraps ``visual-web-agent``'s
``VisualJobExtractor`` so it conforms to the ``AsyncVisualJobExtractor``
protocol used by ``WebTaskWorkflow``.

.. note::
    The real visual provider uses its own Playwright browser to fetch pages.
    When ``uses_own_browser`` is ``True``, ``WebTaskWorkflow._browser_node``
    skips the workflow browser for seed URLs — eliminating double-fetch.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from web_task_agent.models import BrowserPage
from web_task_agent.visual_extractor import VisualExtractionResult, VisualJobFields


class VisualProviderConfigurationError(RuntimeError):
    """Raised when the visual provider cannot be configured (missing package, etc.)."""


@dataclass
class QwenVisualExtractorAdapter:
    """Wraps a ``visual_web_agent`` extractor to match the Agent protocol.

    The adapter translates:
    - ``extract(url: str) → ExtractionResult`` (visual-web-agent)
    - into ``extract(page: BrowserPage) → VisualExtractionResult`` (Agent)

    It uses its own Playwright browser for page fetching, so the workflow
    browser is bypassed for seed URLs when this adapter is active.
    """

    extractor: Any  # visual_web_agent.extractor.VisualJobExtractor

    # Signal to WebTaskWorkflow: this extractor fetches pages on its own.
    uses_own_browser: bool = True

    # Exposed for CLI metadata recording.
    provider: str = "qwen-vl"
    model: str = "qwen-vl-plus"

    async def extract(self, page: BrowserPage) -> VisualExtractionResult:
        result = await self.extractor.extract(page.url)
        if not getattr(result, "success", False) or getattr(result, "job", None) is None:
            return VisualExtractionResult(
                url=page.url,
                success=False,
                fields=None,
                error=getattr(result, "error", ""),
            )
        job = result.job
        fields = VisualJobFields(
            title=getattr(job, "title", ""),
            company=getattr(job, "company", ""),
            location=getattr(job, "location", ""),
            requirements=getattr(job, "requirements", ""),
            responsibilities=getattr(job, "responsibilities", ""),
            skills=list(getattr(job, "skills", [])),
            confidence=getattr(job, "confidence", 0.0),
        )
        return VisualExtractionResult(url=page.url, success=True, fields=fields)

    async def close(self) -> None:
        """Release Playwright browser resources held by the underlying extractor."""
        if hasattr(self.extractor, "close"):
            await self.extractor.close()


def import_visual_web_agent():
    """Lazy-import the sibling ``visual-web-agent`` package.

    Returns the ``visual_web_agent.factory`` module, or raises a clear
    configuration error with install instructions.
    """
    try:
        return import_module("visual_web_agent.factory")
    except ModuleNotFoundError as exc:
        raise VisualProviderConfigurationError(
            "visual-web-agent is required for --visual-extractor-provider qwen-vl. "
            "Install it into the same virtualenv with: "
            "pip install -e ..\\visual-web-agent"
        ) from exc


def build_configured_visual_extractor(
    *,
    provider: str,
    extractor_factory=None,
    model: str | None = None,
) -> QwenVisualExtractorAdapter:
    """Build a real visual extractor adapter from the configured provider.

    Parameters
    ----------
    provider:
        Provider name. Currently only ``"qwen-vl"`` is supported.
    extractor_factory:
        Optional callable that returns a pre-built external extractor.
        When ``None``, the factory is imported from ``visual-web-agent``.
    model:
        Override the default VLM model name.
    """
    if provider != "qwen-vl":
        raise VisualProviderConfigurationError(
            f"Unsupported visual provider: {provider}"
        )
    if extractor_factory is None:
        api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        if not api_key:
            raise VisualProviderConfigurationError(
                "DASHSCOPE_API_KEY is required for --visual-extractor-provider qwen-vl. "
                "Set it in your .env file or as an environment variable."
            )
        factory = import_visual_web_agent()
        external_extractor = factory.build_visual_job_extractor(
            api_key=api_key,
            model=model or "qwen-vl-plus",
        )
    else:
        external_extractor = extractor_factory()
    return QwenVisualExtractorAdapter(
        extractor=external_extractor,
        provider=provider,
        model=model or "qwen-vl-plus",
    )
