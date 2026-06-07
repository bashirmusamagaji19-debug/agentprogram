from web_task_agent.models import BrowserPage


JOB_PAGES = [
    BrowserPage(
        url="https://example.com/jobs/ai-engineering-intern",
        title="AI Engineering Intern at Example AI",
        content=(
            "Remote internship building AI task agents. Requirements include "
            "Python, LangGraph, and LLM application development."
        ),
        source="fixture",
    ),
    BrowserPage(
        url="https://example.com/jobs/ml-platform-intern",
        title="ML Platform Intern at DataWorks",
        content=(
            "Shanghai internship on ML platform services. Requirements include "
            "Python, FastAPI, and SQL."
        ),
        source="fixture",
    ),
]
