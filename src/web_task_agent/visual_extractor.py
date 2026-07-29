"""Visual extraction adapter for screenshot/VLM-style job field extraction.

This module provides a narrow experimental path for visual job extraction
that converts screenshot-derived fields into the existing ``JobPosting`` model.
It keeps the first integration independent from the external ``visual-web-agent``
package — only deterministic demo fixtures are included in this milestone.

.. note::
    The ``VisualJobFields`` model here mirrors ``visual_web_agent.models.VisualJobFields``
    intentionally to avoid a cross-project dependency. See the work-log for the
    deduplication plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from web_task_agent.models import BrowserPage, JobPosting


class VisualJobFields(BaseModel):
    """Structured job fields extracted visually from a page screenshot.

    Mirrors ``visual_web_agent.models.VisualJobFields`` to keep the Agent
    project self-contained during the experimental integration phase.
    """

    title: str = ""
    company: str = ""
    location: str = ""
    requirements: str = ""
    responsibilities: str = ""
    skills: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


@dataclass
class VisualExtractionResult:
    """Outcome of a single visual extraction attempt."""

    url: str
    success: bool
    fields: VisualJobFields | None
    error: str = ""


class AsyncVisualJobExtractor(Protocol):
    """Protocol for async visual job field extractors.

    Implementations can be deterministic fakes (for testing), Playwright +
    Qwen-VL clients, or any other screenshot-to-fields pipeline.
    """

    async def extract(self, page: BrowserPage) -> VisualExtractionResult:
        """Extract visual job fields from a browser page."""


def job_from_visual_fields(*, page: BrowserPage, fields: VisualJobFields) -> JobPosting:
    """Convert visual extraction result into a ``JobPosting``.

    Fields that are empty in the visual result fall back to page metadata
    (title) or safe defaults (company, location) so the downstream verifier
    and matcher always receive a complete model.
    """
    requirements = fields.requirements.strip()
    skills = fields.skills or [
        skill.strip()
        for skill in requirements.replace("\uFF0C", ",").split(",")
        if skill.strip()
    ]
    return JobPosting(
        title=fields.title.strip() or page.title or "Unknown Title",
        company=fields.company.strip() or "Unknown Company",
        location=fields.location.strip() or "Unknown Location",
        source="visual-demo",
        url=page.url,
        requirements=requirements,
        responsibilities=fields.responsibilities.strip(),
        skills=skills,
        confidence=fields.confidence,
    )


class DemoVisualJobExtractor:
    """Deterministic visual extractor with pre-configured fixtures.

    Returns structured ``VisualJobFields`` for known seed URLs and a failure
    result for unknown URLs, so the workflow can fall back to text extraction.

    This is the visual counterpart of ``DemoLlmFieldExtractor`` — it proves
    the adapter interface without calling any external VLM API.
    """

    # Does NOT fetch pages on its own — relies on the workflow browser
    # to provide BrowserPage objects.  Real providers set this to True.
    uses_own_browser: bool = False

    def __init__(self) -> None:
        self._fixtures: dict[str, VisualJobFields] = {
            "https://example.com/jobs/visual-ai-intern": VisualJobFields(
                title="Visual AI Intern",
                company="Example Vision",
                location="Remote",
                requirements="Python, Playwright, Qwen-VL",
                responsibilities="Extract job fields from screenshots",
                skills=["Python", "Playwright", "Qwen-VL"],
                confidence=0.86,
            ),
            "https://example.com/jobs/unstructured-ai-agent-intern": VisualJobFields(
                title="AI Agent Intern",
                company="Example Robotics",
                location="Remote",
                requirements="Python, LangGraph, LLM evaluation",
                responsibilities="Build browser agents from screenshot evidence",
                skills=["Python", "LangGraph", "LLM evaluation"],
                confidence=0.84,
            ),
        }

    async def extract(self, page: BrowserPage) -> VisualExtractionResult:
        fields = self._fixtures.get(page.url)
        if fields is None:
            return VisualExtractionResult(
                url=page.url,
                success=False,
                fields=None,
                error=f"No demo visual fixture for URL: {page.url}",
            )
        return VisualExtractionResult(url=page.url, success=True, fields=fields)
