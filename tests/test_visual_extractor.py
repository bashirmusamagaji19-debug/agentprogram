"""Unit tests for the visual extraction adapter."""

import pytest

from web_task_agent.models import BrowserPage
from web_task_agent.visual_extractor import (
    DemoVisualJobExtractor,
    VisualJobFields,
    job_from_visual_fields,
)


def test_job_from_visual_fields_maps_to_job_posting():
    """All non-empty visual fields are transferred to the JobPosting model."""
    page = BrowserPage(
        url="https://example.com/jobs/visual-ai-intern",
        title="Careers",
        content="",
        source="visual-demo",
    )
    fields = VisualJobFields(
        title="Visual AI Intern",
        company="Example Vision",
        location="Remote",
        requirements="Python, Playwright, Qwen-VL",
        responsibilities="Extract job fields from screenshots",
        skills=["Python", "Playwright", "Qwen-VL"],
        confidence=0.83,
    )

    job = job_from_visual_fields(page=page, fields=fields)

    assert job.title == "Visual AI Intern"
    assert job.company == "Example Vision"
    assert job.location == "Remote"
    assert job.source == "visual-demo"
    assert job.url == "https://example.com/jobs/visual-ai-intern"
    assert job.requirements == "Python, Playwright, Qwen-VL"
    assert job.responsibilities == "Extract job fields from screenshots"
    assert job.skills == ["Python", "Playwright", "Qwen-VL"]
    assert job.confidence == 0.83


def test_job_from_visual_fields_fills_safe_unknowns():
    """Empty fields fall back to page title, 'Unknown Company', or derived skills."""
    page = BrowserPage(
        url="https://example.com/jobs/partial",
        title="Fallback Screenshot Title",
        content="",
        source="visual-demo",
    )
    fields = VisualJobFields(requirements="Python, LLM", confidence=0.4)

    job = job_from_visual_fields(page=page, fields=fields)

    assert job.title == "Fallback Screenshot Title"
    assert job.company == "Unknown Company"
    assert job.location == "Unknown Location"
    assert job.skills == ["Python", "LLM"]
    assert job.confidence == 0.4


def test_job_from_visual_fields_derives_skills_from_chinese_commas():
    """Fallback skill parsing handles Chinese commas from VLM text."""
    page = BrowserPage(
        url="https://example.com/jobs/chinese-comma",
        title="Visual AI Intern",
        content="",
        source="visual-demo",
    )
    fields = VisualJobFields(
        requirements="Python， LangGraph，LLM",
        confidence=0.6,
    )

    job = job_from_visual_fields(page=page, fields=fields)

    assert job.skills == ["Python", "LangGraph", "LLM"]


@pytest.mark.asyncio
async def test_demo_visual_extractor_returns_structured_fields_for_known_seed_url():
    """Known fixture URL produces a successful extraction result."""
    extractor = DemoVisualJobExtractor()
    page = BrowserPage(
        url="https://example.com/jobs/visual-ai-intern",
        title="Careers",
        content="",
        source="fixture",
    )

    result = await extractor.extract(page)

    assert result.success is True
    assert result.fields is not None
    assert result.fields.title == "Visual AI Intern"
    assert result.fields.company == "Example Vision"
    assert result.fields.confidence >= 0.8


@pytest.mark.asyncio
async def test_demo_visual_extractor_reports_unknown_url_failure():
    """Unknown URL returns a failure result so the workflow can fall back to text."""
    extractor = DemoVisualJobExtractor()
    page = BrowserPage(
        url="https://example.com/jobs/unknown",
        title="Unknown",
        content="",
        source="fixture",
    )

    result = await extractor.extract(page)

    assert result.success is False
    assert result.fields is None
    assert "No demo visual fixture" in result.error
