from web_task_agent.models import BrowserPage


DEMO_JOB_PAGES = [
    BrowserPage(
        url="https://example.com/jobs/ai-engineering-intern",
        title="AI Engineering Intern at Example AI",
        content=(
            "Title: AI Engineering Intern\n"
            "Company: Example AI\n"
            "Location: Remote\n"
            "Requirements: Python, LangGraph, LLM\n"
            "Responsibilities: Build browser agents and evaluation tools. "
            "Requirements include Python, LangGraph, and LLM application "
            "development.\n"
            "Posted: 2026-06-07\n"
        ),
        source="demo",
    ),
    BrowserPage(
        url="https://example.com/jobs/ml-platform-intern",
        title="ML Platform Intern at DataWorks",
        content=(
            "Title: ML Platform Intern\n"
            "Company: DataWorks\n"
            "Location: Shanghai\n"
            "Requirements: Python, FastAPI, SQL\n"
            "Responsibilities: Build internal AI services. Requirements "
            "include Python, FastAPI, and SQL.\n"
            "Posted: 2026-06-06\n"
        ),
        source="demo",
    ),
    BrowserPage(
        url="https://example.com/jobs/unstructured-ai-agent-intern",
        title="Careers",
        content=(
            "We are hiring an AI Agent Intern at Example Robotics. "
            "This remote role builds LangGraph browser agents. "
            "Candidates need Python, LangGraph, and LLM evaluation."
        ),
        source="demo-unstructured",
    ),
]
