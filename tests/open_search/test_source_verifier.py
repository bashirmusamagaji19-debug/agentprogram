from web_task_agent.open_search.source_verifier import SourceVerifier


def test_official_greenhouse_url_is_trusted():
    verdict = SourceVerifier().verify_url("https://job-boards.greenhouse.io/example/jobs/123")
    assert verdict.trusted is True
    assert verdict.source_type == "public_ats"


def test_search_result_page_is_rejected():
    verdict = SourceVerifier().verify_url("https://www.google.com/search?q=agent+intern")
    assert verdict.trusted is False
    assert verdict.failure_code == "source_untrusted"
