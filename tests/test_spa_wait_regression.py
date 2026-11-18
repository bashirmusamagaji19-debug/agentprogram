from types import SimpleNamespace

import pytest

from web_task_agent import browser as module
from web_task_agent.browser import BrowserUseClient
from web_task_agent.link_verification import (
    JobLinkVerifier,
    _jd_matches,
    _title_matches,
)

JOB_URL = "https://jobs.example.com/positions/123"
JOB_TITLE = "AI Platform Engineer"
JOB_JD = (
    "Build reliable Python services for large language model evaluation and deployment. "
    "Own observability, incident response, and production quality."
)
NAVIGATION_SHELL = (
    "Careers Home Jobs Locations About Us Life At The Company Benefits Students "
    "Contact Privacy Terms Accessibility Cookie Preferences Copyright Example Labs. "
    "Search jobs by keyword, location, team, or category. "
    "Follow the company on social media for the latest news and career opportunities."
)
RENDERED_JOB = f"{JOB_TITLE}\nExample Labs - Shanghai\n{JOB_JD}"


def make_job() -> SimpleNamespace:
    return SimpleNamespace(
        title=JOB_TITLE,
        company="Example Labs",
        location="Shanghai",
        source="official",
        url=JOB_URL,
        requirements="Python, production services, and observability",
        responsibilities=JOB_JD,
        skills=["Python", "LLM", "observability"],
    )


def job_ready(job: SimpleNamespace, content: str, title: str) -> bool:
    return _title_matches(job, title, content) and _jd_matches(job, content)


@pytest.mark.asyncio
async def test_default_wait_does_not_abandon_spa_before_first_render(monkeypatch):
    elapsed = [0.0]

    async def advance(seconds):
        elapsed[0] += seconds

    class Page:
        async def evaluate(self, script):
            return "Actual rendered job description " * 10 if elapsed[0] >= 1.25 else ""

    monkeypatch.setattr(module, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(module.asyncio, "sleep", advance)
    content = await BrowserUseClient()._read_body_until_useful(Page())
    assert "Actual rendered job description" in content


@pytest.mark.asyncio
async def test_job_ready_wait_ignores_a_long_navigation_shell(monkeypatch):
    elapsed = [0.0]

    async def advance(seconds):
        elapsed[0] += seconds

    class Page:
        async def get_title(self):
            return "Careers" if elapsed[0] < 1.25 else JOB_TITLE

        async def evaluate(self, script):
            if "document.body" not in script:
                return ""
            return NAVIGATION_SHELL if elapsed[0] < 1.25 else RENDERED_JOB

    job = make_job()
    monkeypatch.setattr(module, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(module.asyncio, "sleep", advance)
    browser = BrowserUseClient(
        render_wait_seconds=8,
        content_ready=lambda content, title: job_ready(job, content, title),
    )

    content = await browser._read_body_until_useful(Page())

    assert len(NAVIGATION_SHELL) > module.MIN_USEFUL_CONTENT_CHARS
    assert content == RENDERED_JOB
    assert elapsed[0] >= 1.25


@pytest.mark.asyncio
async def test_job_ready_wait_stops_at_twenty_seconds_and_reports_unready_content(monkeypatch):
    elapsed = [0.0]

    async def advance(seconds):
        elapsed[0] += seconds

    class Page:
        async def get_title(self):
            return "Careers"

        async def evaluate(self, script):
            return NAVIGATION_SHELL

    job = make_job()
    monkeypatch.setattr(module, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(module.asyncio, "sleep", advance)
    browser = BrowserUseClient(
        render_wait_seconds=99,
        content_ready=lambda content, title: job_ready(job, content, title),
    )

    content = await browser._read_body_until_useful(Page())

    assert content == NAVIGATION_SHELL
    assert elapsed[0] == pytest.approx(20.0)
    assert not job_ready(job, content, "Careers")


@pytest.mark.asyncio
async def test_job_ready_wait_also_honors_the_total_deadline(monkeypatch):
    elapsed = [0.0]

    async def advance(seconds):
        elapsed[0] += seconds

    class Page:
        async def get_title(self):
            return "Careers"

        async def evaluate(self, script):
            return NAVIGATION_SHELL

    job = make_job()
    monkeypatch.setattr(module, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(module.asyncio, "sleep", advance)
    browser = BrowserUseClient(
        render_wait_seconds=8,
        deadline=2.0,
        content_ready=lambda content, title: job_ready(job, content, title),
    )

    await browser._read_body_until_useful(Page())

    assert elapsed[0] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_generic_render_wait_keeps_the_existing_length_threshold(monkeypatch):
    elapsed = [0.0]

    async def advance(seconds):
        elapsed[0] += seconds

    class Page:
        async def evaluate(self, script):
            return NAVIGATION_SHELL

    monkeypatch.setattr(module, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(module.asyncio, "sleep", advance)

    content = await BrowserUseClient(render_wait_seconds=8)._read_body_until_useful(Page())

    assert content == NAVIGATION_SHELL
    assert elapsed[0] == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("render_after", [1.25, 6.25, 11.25])
async def test_default_verifier_waits_for_the_expected_job_before_verifying(monkeypatch, render_after):
    elapsed = [0.0]

    async def advance(seconds):
        elapsed[0] += seconds

    class Page:
        async def get_title(self):
            return "Careers" if elapsed[0] < render_after else JOB_TITLE

        async def get_url(self):
            return JOB_URL

        async def evaluate(self, script):
            if "document.body" in script:
                return NAVIGATION_SHELL if elapsed[0] < render_after else RENDERED_JOB
            if "querySelectorAll" in script and "getBoundingClientRect" in script:
                if elapsed[0] < render_after:
                    return []
                return [{"tag": "button", "text": "Apply now", "visible": True, "enabled": True}]
            if "window.location.href" in script:
                return JOB_URL
            return ""

    class Session:
        async def start(self):
            return None

        async def new_page(self, url):
            return Page()

        async def close(self):
            return None

    async def http_loader(url):
        return module.BrowserPage(
            url=url,
            title="Careers",
            content=NAVIGATION_SHELL,
            source="http",
            metadata={
                "requested_url": url,
                "final_url": url,
                "final_url_observed": True,
                "status_code": 200,
                "apply_controls": [],
            },
        )

    monkeypatch.setattr(module, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(module.asyncio, "sleep", advance)
    verifier = JobLinkVerifier(http_loader=http_loader, timeout_seconds=20)
    verifier.browser_loader._session_factory = Session

    receipt = await verifier.verify(make_job(), run_id="run-1")

    assert receipt["status"] == "verified"
    assert receipt["method"] == "browser"
    assert receipt["title_matched"] is True
    assert receipt["jd_matched"] is True
    assert elapsed[0] >= render_after
