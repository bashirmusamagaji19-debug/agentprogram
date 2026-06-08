from web_task_agent.models import BrowserPage


JOB_PAGES = [
    BrowserPage(
        url="https://example.com/jobs/ai-engineering-intern",
        title="AI Engineering Intern at Example AI",
        content=(
            "Title: AI Engineering Intern\n"
            "Company: Example AI\n"
            "Location: Remote\n"
            "Requirements: Python, LangGraph, LLM\n"
            "Responsibilities: Remote internship building AI task agents. "
            "Requirements include Python, LangGraph, and LLM application "
            "development.\n"
            "Posted: 2026-06-07\n"
        ),
        source="fixture",
    ),
    BrowserPage(
        url="https://example.com/jobs/ml-platform-intern",
        title="ML Platform Intern at DataWorks",
        content=(
            "Title: ML Platform Intern\n"
            "Company: DataWorks\n"
            "Location: Shanghai\n"
            "Requirements: Python, FastAPI, SQL\n"
            "Responsibilities: Shanghai internship on ML platform services. "
            "Requirements include Python, FastAPI, and SQL.\n"
            "Posted: 2026-06-06\n"
        ),
        source="fixture",
    ),
]

FAKE_JOB_PAGES = JOB_PAGES
