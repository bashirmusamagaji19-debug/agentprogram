import httpx
import pytest

from web_task_agent.open_search.source_verifier import SourceVerifier


def test_official_greenhouse_url_is_trusted():
    verdict = SourceVerifier().verify_url("https://job-boards.greenhouse.io/example/jobs/123")
    assert verdict.trusted is True
    assert verdict.source_type == "public_ats"


def test_search_result_page_is_rejected():
    verdict = SourceVerifier().verify_url("https://www.google.com/search?q=agent+intern")
    assert verdict.trusted is False
    assert verdict.failure_code == "source_untrusted"


def test_greenhouse_board_or_error_page_is_not_a_job_detail():
    verdict = SourceVerifier().verify_url("https://job-boards.greenhouse.io/reddit?error=true")

    assert verdict.trusted is False
    assert verdict.failure_code == "not_job_detail"


def test_arbitrary_public_jobs_path_is_not_implicitly_trusted():
    verdict = SourceVerifier().verify_url("https://unknown.example/jobs/123")

    assert verdict.trusted is False
    assert verdict.failure_code == "source_untrusted"


def test_explicit_official_career_host_is_trusted(monkeypatch):
    monkeypatch.setenv("OPEN_SEARCH_OFFICIAL_HOSTS", "careers.example.com")

    verdict = SourceVerifier().verify_url("https://careers.example.com/jobs/123")

    assert verdict.trusted is True
    assert verdict.source_type == "company_careers"


def test_url_with_userinfo_is_rejected_even_on_public_host():
    verdict = SourceVerifier().verify_url("https://user@careers.example.com/jobs/123")

    assert verdict.trusted is False
    assert verdict.failure_code == "source_untrusted"


def test_private_and_loopback_hosts_are_rejected():
    verifier = SourceVerifier()
    for url in (
        "http://127.0.0.1/jobs/1",
        "http://localhost/jobs/1",
        "http://192.168.1.10/jobs/1",
        "http://[::1]/jobs/1",
        "http://user@127.0.0.1/jobs/1",
    ):
        verdict = verifier.verify_url(url)
        assert verdict.trusted is False
        assert verdict.failure_code == "source_untrusted"


def test_malformed_url_is_rejected_without_raising():
    verdict = SourceVerifier().verify_url("http://[::1/jobs/1")
    assert verdict.trusted is False
    assert verdict.failure_code == "source_untrusted"


def test_page_timeout_setting_is_bounded(monkeypatch):
    monkeypatch.setenv("OPEN_SEARCH_PAGE_TIMEOUT_SECONDS", "0")
    assert SourceVerifier().timeout_seconds == 1.0
    monkeypatch.setenv("OPEN_SEARCH_PAGE_TIMEOUT_SECONDS", "bad")
    assert SourceVerifier().timeout_seconds == 10.0


def test_redirect_limit_setting_is_bounded(monkeypatch):
    monkeypatch.setenv("OPEN_SEARCH_MAX_REDIRECTS", "99")
    assert SourceVerifier().max_redirects == 10
    monkeypatch.setenv("OPEN_SEARCH_MAX_REDIRECTS", "bad")
    assert SourceVerifier().max_redirects == 5


@pytest.mark.asyncio
async def test_reachability_verifier_accepts_html_detail_page():
    seen_headers = {}
    page_html = "<html><body>job detail</body></html>"

    async def handler(request):
        seen_headers.update(request.headers)
        return httpx.Response(
            200,
            content=page_html.encode(),
            headers={"content-type": "text/html"},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        verdict = await SourceVerifier().verify_reachable(
            "https://job-boards.greenhouse.io/example/jobs/123", client=client
        )
    finally:
        await client.aclose()
    assert verdict.trusted is True
    assert len(verdict.content_hash) == 64
    assert verdict.page_html == page_html
    assert seen_headers["user-agent"].startswith("OpenWebJobSearchAgent/")


@pytest.mark.asyncio
async def test_reachability_verifier_rejects_non_html_page():
    async def handler(request):
        return httpx.Response(200, headers={"content-type": "application/json"}, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        verdict = await SourceVerifier().verify_reachable(
            "https://job-boards.greenhouse.io/example/jobs/123", client=client
        )
    finally:
        await client.aclose()
    assert verdict.failure_code == "page_not_html"


@pytest.mark.asyncio
async def test_reachability_verifier_rejects_untrusted_redirect():
    requested_urls = []

    async def handler(request):
        requested_urls.append(str(request.url))
        if "greenhouse" in request.url.host:
            return httpx.Response(
                302,
                headers={"location": "https://evil.example/jobs/1"},
                request=request,
            )
        return httpx.Response(
            200,
            content=b"<html>redirected</html>",
            headers={"content-type": "text/html"},
            request=request,
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )
    try:
        verdict = await SourceVerifier().verify_reachable(
            "https://job-boards.greenhouse.io/example/jobs/123", client=client
        )
    finally:
        await client.aclose()
    assert verdict.failure_code == "redirect_untrusted"
    assert requested_urls == ["https://job-boards.greenhouse.io/example/jobs/123"]


@pytest.mark.asyncio
async def test_reachability_verifier_allows_redirect_within_same_ats_family():
    requested_hosts = []

    async def handler(request):
        requested_hosts.append(request.url.host)
        if request.url.host == "job-boards.greenhouse.io":
            return httpx.Response(
                302,
                headers={"location": "https://boards.greenhouse.io/example/jobs/123"},
                request=request,
            )
        return httpx.Response(
            200,
            content=b"<html>trusted detail</html>",
            headers={"content-type": "text/html"},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        verdict = await SourceVerifier().verify_reachable(
            "https://job-boards.greenhouse.io/example/jobs/123", client=client
        )
    finally:
        await client.aclose()

    assert verdict.trusted is True
    assert verdict.normalized_url == "https://boards.greenhouse.io/example/jobs/123"
    assert requested_hosts == ["job-boards.greenhouse.io", "boards.greenhouse.io"]


@pytest.mark.asyncio
async def test_reachability_verifier_preserves_not_job_detail_redirect_reason():
    requested_urls = []

    async def handler(request):
        requested_urls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"location": "https://job-boards.greenhouse.io/example?error=true"},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        verdict = await SourceVerifier().verify_reachable(
            "https://job-boards.greenhouse.io/example/jobs/123", client=client
        )
    finally:
        await client.aclose()

    assert verdict.failure_code == "not_job_detail"
    assert requested_urls == ["https://job-boards.greenhouse.io/example/jobs/123"]
