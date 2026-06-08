from web_task_agent.extractor import PageExtractor
from web_task_agent.models import BrowserPage


LABELED_JOB_PAGE = BrowserPage(
    url="https://example.com/jobs/ai-engineering-intern",
    title="AI Engineering Intern at Example AI",
    content=(
        "Title: AI Engineering Intern\n"
        "Company: Example AI\n"
        "Location: Remote\n"
        "Requirements: Python, LangGraph, LLM\n"
        "Responsibilities: Build AI task agents\n"
        "Posted At: 2026-06-07\n"
    ),
    source="fixture",
)


def test_extract_job_from_labeled_page_content():
    extractor = PageExtractor()

    job = extractor.extract(LABELED_JOB_PAGE)

    assert job.title == "AI Engineering Intern"
    assert job.company == "Example AI"
    assert job.location == "Remote"
    assert job.skills == ["Python", "LangGraph", "LLM"]
    assert job.posted_at == "2026-06-07"
    assert job.confidence >= 0.8


def test_extract_job_uses_page_title_when_title_label_missing():
    page = LABELED_JOB_PAGE.model_copy(
        update={
            "title": "Fallback AI Intern",
            "content": (
                "Company: Example AI\n"
                "Location: Remote\n"
                "Requirements: Python, LLM\n"
                "Responsibilities: Build AI tools\n"
            ),
        }
    )
    extractor = PageExtractor()

    job = extractor.extract(page)

    assert job.title == "Fallback AI Intern"
    assert job.confidence >= 0.6


def test_extract_job_label_matching_is_case_insensitive_and_whitespace_tolerant():
    page = LABELED_JOB_PAGE.model_copy(
        update={
            "content": (
                "  job title  : Applied AI Intern\n"
                "  employer : Example Labs\n"
                " city : Shanghai\n"
                " SKILLS : Python, SQL\n"
                " role : Ship AI workflows\n"
                " posted : 2026-06-01\n"
            )
        }
    )
    extractor = PageExtractor()

    job = extractor.extract(page)

    assert job.title == "Applied AI Intern"
    assert job.company == "Example Labs"
    assert job.location == "Shanghai"
    assert job.requirements == "Python, SQL"
    assert job.responsibilities == "Ship AI workflows"
    assert job.posted_at == "2026-06-01"


def test_extract_job_splits_skills_on_chinese_commas():
    page = LABELED_JOB_PAGE.model_copy(
        update={
            "content": (
                "Title: AI Intern\n"
                "Company: Example AI\n"
                "Location: Remote\n"
                "Requirements: Python\uFF0C LangGraph\uFF0CLLM\n"
            )
        }
    )
    extractor = PageExtractor()

    job = extractor.extract(page)

    assert job.skills == ["Python", "LangGraph", "LLM"]


def test_extract_job_missing_company_and_location_use_unknowns_and_lower_confidence():
    complete_job = PageExtractor().extract(LABELED_JOB_PAGE)
    page = LABELED_JOB_PAGE.model_copy(
        update={
            "content": (
                "Title: AI Engineering Intern\n"
                "Requirements: Python, LangGraph, LLM\n"
                "Responsibilities: Build AI task agents\n"
            )
        }
    )
    extractor = PageExtractor()

    job = extractor.extract(page)

    assert job.company == "Unknown Company"
    assert job.location == "Unknown Location"
    assert job.confidence < complete_job.confidence


def test_extract_job_missing_title_uses_unknown_and_lower_confidence():
    complete_job = PageExtractor().extract(LABELED_JOB_PAGE)
    page = LABELED_JOB_PAGE.model_copy(
        update={
            "title": "",
            "content": (
                "Company: Example AI\n"
                "Location: Remote\n"
                "Requirements: Python, LangGraph, LLM\n"
                "Responsibilities: Build AI task agents\n"
            ),
        }
    )
    extractor = PageExtractor()

    job = extractor.extract(page)

    assert job.title == "Unknown Title"
    assert job.confidence < complete_job.confidence


def test_extract_job_preserves_model_skill_dedupe():
    page = LABELED_JOB_PAGE.model_copy(
        update={
            "content": (
                "Title: AI Intern\n"
                "Company: Example AI\n"
                "Location: Remote\n"
                "Requirements: Python, python, LLM\n"
                "Responsibilities: Build AI tools\n"
            )
        }
    )
    extractor = PageExtractor()

    job = extractor.extract(page)

    assert job.skills == ["Python", "LLM"]
