from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


@dataclass(frozen=True)
class SourceVerdict:
    trusted: bool
    normalized_url: str
    source_type: str
    reason: str
    failure_code: str | None = None
    content_hash: str = ""


class SourceVerifier:
    _ats = ("greenhouse.io", "lever.co", "myworkdayjobs.com", "ashbyhq.com")
    _search_hosts = ("google.", "bing.com", "baidu.com", "duckduckgo.com")

    def verify_url(self, url: str) -> SourceVerdict:
        normalized = url.strip()
        parsed = urlparse(normalized)
        try:
            host = (parsed.hostname or "").casefold()
        except ValueError:
            host = ""
        if parsed.scheme not in {"http", "https"} or not host:
            return SourceVerdict(
                False, normalized, "invalid", "URL scheme or host is invalid", "source_untrusted"
            )
        if any(marker in host for marker in self._search_hosts) or parsed.path.startswith(
            "/search"
        ):
            return SourceVerdict(
                False,
                normalized,
                "search_engine",
                "search result pages are not job details",
                "source_untrusted",
            )
        if host in {"localhost", "localhost.localdomain"}:
            return SourceVerdict(
                False,
                normalized,
                "private_host",
                "localhost is not a public job source",
                "source_untrusted",
            )
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        ):
            return SourceVerdict(
                False,
                normalized,
                "private_host",
                "private or reserved IPs are not public job sources",
                "source_untrusted",
            )
        if any(host == suffix or host.endswith("." + suffix) for suffix in self._ats):
            return SourceVerdict(True, normalized, "public_ats", "known public ATS detail host")
        if any(
            token in parsed.path.casefold() for token in ("/careers", "/jobs", "/job/", "/recruit")
        ):
            return SourceVerdict(
                True, normalized, "company_careers", "career detail path on a public host"
            )
        return SourceVerdict(
            False,
            normalized,
            "unknown",
            "host is not a trusted job detail source",
            "source_untrusted",
        )

    def _same_trusted_host_family(self, original_url: str, final_url: str) -> bool:
        original_host = (urlparse(original_url).hostname or "").casefold()
        final_host = (urlparse(final_url).hostname or "").casefold()
        if original_host == final_host:
            return True
        return any(
            original_host == suffix
            and final_host.endswith("." + suffix)
            or final_host == suffix
            and original_host.endswith("." + suffix)
            for suffix in self._ats
        )

    async def verify_reachable(
        self, url: str, *, client: httpx.AsyncClient | None = None
    ) -> SourceVerdict:
        verdict = self.verify_url(url)
        if not verdict.trusted:
            return verdict
        own_client = client is None
        request_client = client or httpx.AsyncClient(timeout=10, follow_redirects=True)
        try:
            response = await request_client.get(url)
            if response.status_code >= 400:
                return SourceVerdict(
                    False,
                    verdict.normalized_url,
                    verdict.source_type,
                    f"detail page returned HTTP {response.status_code}",
                    "page_unreachable",
                )
            final_verdict = self.verify_url(str(response.url))
            if not final_verdict.trusted or not self._same_trusted_host_family(
                verdict.normalized_url, str(response.url)
            ):
                return SourceVerdict(
                    False,
                    str(response.url),
                    final_verdict.source_type,
                    "detail page redirected to an untrusted URL",
                    "redirect_untrusted",
                )
            content_type = response.headers.get("content-type", "").casefold()
            if content_type and "text/html" not in content_type:
                return SourceVerdict(
                    False,
                    verdict.normalized_url,
                    verdict.source_type,
                    "detail page did not return HTML",
                    "page_not_html",
                )
            page_text = response.text.strip()
            if not page_text:
                return SourceVerdict(
                    False,
                    verdict.normalized_url,
                    verdict.source_type,
                    "detail page returned an empty body",
                    "page_empty",
                )
            return SourceVerdict(
                verdict.trusted,
                verdict.normalized_url,
                verdict.source_type,
                verdict.reason,
                verdict.failure_code,
                hashlib.sha256(page_text.encode("utf-8")).hexdigest(),
            )
        except httpx.HTTPError as exc:
            return SourceVerdict(
                False,
                verdict.normalized_url,
                verdict.source_type,
                f"detail page request failed: {type(exc).__name__}",
                "page_unreachable",
            )
        finally:
            if own_client:
                await request_client.aclose()
