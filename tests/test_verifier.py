from web_task_agent.models import JobPosting
from web_task_agent.verifier import JobVerifier


def make_job(**overrides):
    data = {
        "title": "AI Engineering Intern",
        "company": "Example AI",
        "location": "Remote",
        "source": "fixture",
        "url": "https://example.com/jobs/1",
        "requirements": "Python, LangGraph, LLM",
        "responsibilities": "Build AI agents",
        "skills": ["Python", "LangGraph", "LLM"],
        "confidence": 0.9,
    }
    data.update(overrides)
    return JobPosting(**data)


def test_verifier_accepts_relevant_complete_job():
    verifier = JobVerifier(required_keywords=["AI", "LLM", "Agent"])

    result = verifier.verify(make_job())

    assert result.is_valid is True
    assert result.reasons == []


def test_verifier_rejects_low_confidence_job():
    verifier = JobVerifier(required_keywords=["AI"])

    result = verifier.verify(make_job(confidence=0.3))

    assert result.is_valid is False
    assert "confidence below 0.5" in result.reasons


def test_verifier_rejects_missing_requirements_and_responsibilities():
    verifier = JobVerifier(required_keywords=["AI"])

    result = verifier.verify(make_job(requirements="", responsibilities=""))

    assert result.is_valid is False
    assert "missing requirements and responsibilities" in result.reasons


def test_verifier_rejects_irrelevant_job_case_insensitively():
    verifier = JobVerifier(required_keywords=["LLM", "Agent"])

    result = verifier.verify(
        make_job(
            title="Backend Intern",
            requirements="Java, Spring",
            responsibilities="Build payment services",
            skills=["Java"],
        )
    )

    assert result.is_valid is False
    assert "not relevant to AI internship direction" in result.reasons


def test_dedupe_removes_same_company_title_pair():
    verifier = JobVerifier(required_keywords=["AI"])
    first = make_job(url="https://example.com/jobs/1")
    duplicate = make_job(url="https://example.com/jobs/2")

    unique, duplicates = verifier.dedupe([first, duplicate])

    assert unique == [first]
    assert duplicates == [duplicate]


def test_dedupe_removes_exact_duplicate_url_before_title_pair():
    verifier = JobVerifier(required_keywords=["AI"])
    first = make_job(url="https://example.com/jobs/1")
    duplicate = make_job(
        title="Different AI Intern",
        company="Other AI",
        url="https://example.com/jobs/1",
    )

    unique, duplicates = verifier.dedupe([first, duplicate])

    assert unique == [first]
    assert duplicates == [duplicate]
