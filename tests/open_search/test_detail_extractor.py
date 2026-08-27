import pytest

from web_task_agent.open_search.detail_extractor import (
    DetailExtractionError,
    extract_verified_job,
)
from web_task_agent.open_search.evidence import build_content_hash

JOB_HTML = """
<html>
  <head>
    <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Agent Engineering Intern",
        "hiringOrganization": {"@type": "Organization", "name": "Example AI"},
        "jobLocation": {
          "@type": "Place",
          "address": {
            "@type": "PostalAddress",
            "addressLocality": "Beijing",
            "addressCountry": "CN"
          }
        },
        "employmentType": "INTERN",
        "description": "<p>Build evidence-backed agents.</p>",
        "responsibilities": "Develop LangGraph workflows",
        "qualifications": "Python and FastAPI",
        "skills": ["Python", "LangGraph"]
      }
    </script>
  </head>
  <body><h1>Agent Engineering Intern</h1></body>
</html>
""".strip()


def test_extract_verified_job_uses_detail_page_fields_and_page_hash():
    page_hash = build_content_hash(JOB_HTML)

    job = extract_verified_job(
        JOB_HTML,
        page_url="https://job-boards.greenhouse.io/example/jobs/123",
        source_type="public_ats",
        content_hash=page_hash,
    )

    assert job.title == "Agent Engineering Intern"
    assert job.company == "Example AI"
    assert job.location == "Beijing, CN"
    assert job.employment_type == "INTERN"
    assert job.requirements == "Python and FastAPI"
    assert job.responsibilities == "Develop LangGraph workflows"
    assert job.skills == ["Python", "LangGraph"]
    assert {evidence.field_name for evidence in job.evidence} >= {
        "title",
        "company",
        "location",
        "requirements",
    }
    assert all(evidence.content_hash == page_hash for evidence in job.evidence)
    assert all(evidence.page_url == job.url for evidence in job.evidence)


def test_extract_verified_job_rejects_page_without_jobposting():
    html = "<html><body><h1>Careers</h1></body></html>"

    with pytest.raises(DetailExtractionError) as exc_info:
        extract_verified_job(
            html,
            page_url="https://example.com/careers",
            source_type="company_careers",
            content_hash=build_content_hash(html),
        )

    assert exc_info.value.code == "extraction_incomplete"


def test_extract_verified_job_rejects_missing_required_core_field():
    html = """
    <script type="application/ld+json">
      {"@type":"JobPosting","title":"Agent Intern","hiringOrganization":{"name":"AI"}}
    </script>
    """

    with pytest.raises(DetailExtractionError) as exc_info:
        extract_verified_job(
            html,
            page_url="https://example.com/jobs/1",
            source_type="company_careers",
            content_hash=build_content_hash(html),
        )

    assert exc_info.value.code == "extraction_incomplete"
    assert "location" in str(exc_info.value)


def test_extract_verified_job_supports_greenhouse_open_graph_metadata():
    html = """
    <html><head>
      <title>Job Application for Agent Engineer Intern at Example AI</title>
      <meta property="og:title" content="Agent Engineer Intern">
      <meta property="og:description" content="Shanghai, China">
    </head><body><main><h1>Agent Engineer Intern</h1>
    <p>Build reliable agents.</p></main></body></html>
    """.strip()

    job = extract_verified_job(
        html,
        page_url="https://job-boards.greenhouse.io/example/jobs/123",
        source_type="public_ats",
        content_hash=build_content_hash(html),
    )

    assert job.title == "Agent Engineer Intern"
    assert job.company == "Example AI"
    assert job.location == "Shanghai, China"
    assert "Build reliable agents" in job.description
    assert job.metadata["extraction_method"] == "greenhouse_open_graph"


def test_greenhouse_open_graph_company_prefix_is_case_insensitive():
    html = """
    <html><head>
      <title>job application for Agent Intern at Example AI</title>
      <meta property="og:title" content="Agent Intern">
      <meta property="og:description" content="Remote">
    </head><body><p>Build reliable agents.</p></body></html>
    """.strip()

    job = extract_verified_job(
        html,
        page_url="https://job-boards.greenhouse.io/example/jobs/123",
        source_type="public_ats",
        content_hash=build_content_hash(html),
    )

    assert job.company == "Example AI"


def test_extract_verified_job_bounds_persisted_description_and_evidence_snippets():
    html = f"""
    <html><head>
      <title>Job Application for Agent Intern at Example AI</title>
      <meta property="og:title" content="Agent Intern">
      <meta property="og:description" content="Remote">
    </head><body><main>{'x' * 12000}</main></body></html>
    """.strip()

    job = extract_verified_job(
        html,
        page_url="https://job-boards.greenhouse.io/example/jobs/123",
        source_type="public_ats",
        content_hash=build_content_hash(html),
    )

    assert len(job.description) == 8000
    assert max(len(evidence.snippet) for evidence in job.evidence) <= 500
