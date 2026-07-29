from __future__ import annotations

from web_task_agent.search_discovery import discover_job_links


def test_discovery_decodes_google_redirect_and_keeps_direct_job_links():
    html = """
    <html><body>
      <a href="/url?q=https%3A%2F%2Fjob-boards.greenhouse.io%2Facme%2Fjobs%2F123&sa=U">Role</a>
      <a href="https://jobs.lever.co/acme/abc?utm_source=google">Another role</a>
    </body></html>
    """

    assert discover_job_links(
        html,
        base_url="https://www.google.com/search?q=AI+intern",
    ) == [
        "https://job-boards.greenhouse.io/acme/jobs/123",
        "https://jobs.lever.co/acme/abc",
    ]


def test_discovery_filters_duplicates_unsafe_and_non_job_links():
    raw_links = [
        "javascript:alert(1)",
        "https://www.google.com/preferences",
        "https://example.com/about",
        "https://example.com/careers/ai-intern?utm_campaign=test",
        "https://example.com/careers/ai-intern#description",
    ]

    assert discover_job_links(
        "",
        base_url="https://www.google.com/search?q=AI+intern",
        raw_links=raw_links,
    ) == ["https://example.com/careers/ai-intern"]


def test_discovery_resolves_relative_job_links():
    html = '<a href="/jobs/ai-agent-intern">AI Agent Intern</a>'

    assert discover_job_links(html, base_url="https://careers.example.com/search") == [
        "https://careers.example.com/jobs/ai-agent-intern"
    ]
