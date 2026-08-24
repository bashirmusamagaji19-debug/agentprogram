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


@pytest.mark.asyncio
async def test_reachability_verifier_accepts_html_detail_page():
    async def handler(request):
        return httpx.Response(
            200,
            content=b"<html><body>job detail</body></html>",
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
    async def handler(request):
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
