from __future__ import annotations

from web_task_agent.models import BrowserPage


PUBLIC_JOB_FIXTURE_PAGES = [
    BrowserPage(
        url="https://boards.greenhouse.io/example/jobs/ai-agent-intern",
        title="AI Agent Engineering Intern - Example Robotics",
        content=(
            "AI Agent Engineering Intern\n"
            "Example Robotics\n"
            "Remote - US\n"
            "About the role\n"
            "You will build browser automation agents for AI workflows.\n"
            "Qualifications\n"
            "Python, LangGraph, browser-use, LLM evaluation\n"
            "Posted June 8, 2026\n"
        ),
        source="greenhouse-fixture",
    ),
    BrowserPage(
        url="https://jobs.lever.co/example/llm-application-intern",
        title="LLM Application Intern",
        content=(
            "LLM Application Intern\n"
            "Example AI Lab · Shanghai\n"
            "Responsibilities\n"
            "Prototype RAG and agent workflows for internal AI applications.\n"
            "Requirements\n"
            "Python, FastAPI, RAG, evaluation\n"
        ),
        source="lever-fixture",
    ),
]
