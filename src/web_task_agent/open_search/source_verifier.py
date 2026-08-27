from __future__ import annotations

import hashlib
import ipaddress
import os
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx


@dataclass(frozen=True)
class SourceVerdict:
    trusted: bool
    normalized_url: str
    source_type: str
    reason: str
    failure_code: str | None = None
    content_hash: str = ""
    page_html: str = ""


class SourceVerifier:
    _user_agent = "OpenWebJobSearchAgent/0.1 (+https://github.com/)"
    _ats = ("greenhouse.io", "lever.co", "myworkdayjobs.com", "ashbyhq.com")
    _search_hosts = ("google.", "bing.com", "baidu.com", "duckduckgo.com")

    def __init__(
        self,
        *,
        timeout_seconds: float | None = None,
        official_hosts: tuple[str, ...] | None = None,
    ) -> None:
        if timeout_seconds is not None:
            self.timeout_seconds = max(1.0, timeout_seconds)
        else:
            try:
                configured = float(os.getenv("OPEN_SEARCH_PAGE_TIMEOUT_SECONDS", "10"))
            except ValueError:
                configured = 10.0
            self.timeout_seconds = max(1.0, configured)
        self.max_redirects = self._read_max_redirects()
        configured_hosts = (
            official_hosts
            if official_hosts is not None
            else tuple(os.getenv("OPEN_SEARCH_OFFICIAL_HOSTS", "").split(","))
        )
        self.official_hosts = frozenset(
            host.strip().casefold() for host in configured_hosts if host.strip()
        )

    @staticmethod
    def _read_max_redirects() -> int:
        try:
            value = int(os.getenv("OPEN_SEARCH_MAX_REDIRECTS", "5"))
        except ValueError:
            value = 5
        return max(0, min(10, value))

    def verify_url(self, url: str) -> SourceVerdict:
        normalized = url.strip()
        try:
            parsed = urlparse(normalized)
        except ValueError:
            return SourceVerdict(
                False,
                normalized,
                "invalid",
                "URL could not be parsed",
                "source_untrusted",
            )
        try:
            host = (parsed.hostname or "").casefold()
        except ValueError:
            host = ""
        if parsed.scheme not in {"http", "https"} or not host:
            return SourceVerdict(
                False, normalized, "invalid", "URL scheme or host is invalid", "source_untrusted"
            )
        if parsed.username is not None or parsed.password is not None:
            return SourceVerdict(
                False,
                normalized,
                "invalid",
                "URLs containing userinfo are not allowed",
                "source_untrusted",
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
        ats_suffix = next(
            (suffix for suffix in self._ats if host == suffix or host.endswith("." + suffix)),
            None,
        )
        if ats_suffix and not self._is_ats_detail_path(ats_suffix, parsed.path):
            return SourceVerdict(
                False,
                normalized,
                "public_ats",
                "ATS page is not an individual job detail",
                "not_job_detail",
            )
        if ats_suffix:
            return SourceVerdict(True, normalized, "public_ats", "known public ATS detail host")
        if host in self.official_hosts and any(
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

    @staticmethod
    def _is_ats_detail_path(suffix: str, path: str) -> bool:
        normalized = path.casefold().rstrip("/")
        parts = [part for part in normalized.split("/") if part]
        if suffix == "greenhouse.io":
            return "/jobs/" in normalized and len(parts) >= 3
        if suffix == "lever.co":
            return len(parts) >= 2
        if suffix == "myworkdayjobs.com":
            return "/job/" in normalized
        if suffix == "ashbyhq.com":
            return len(parts) >= 2
        return False

    def _same_trusted_host_family(self, original_url: str, final_url: str) -> bool:
        original_host = (urlparse(original_url).hostname or "").casefold()
        final_host = (urlparse(final_url).hostname or "").casefold()
        if original_host == final_host:
            return True
        return any(
            (original_host == suffix or original_host.endswith("." + suffix))
            and (final_host == suffix or final_host.endswith("." + suffix))
            for suffix in self._ats
        )

    async def verify_reachable(
        self, url: str, *, client: httpx.AsyncClient | None = None
    ) -> SourceVerdict:
        verdict = self.verify_url(url)
        if not verdict.trusted:
            return verdict
        own_client = client is None
        request_client = client or httpx.AsyncClient(
            timeout=self.timeout_seconds, follow_redirects=False
        )
        try:
            current_url = verdict.normalized_url
            response = None
            for redirect_count in range(self.max_redirects + 1):
                response = await request_client.get(
                    current_url,
                    headers={"User-Agent": self._user_agent},
                    follow_redirects=False,
                )
                if not response.is_redirect:
                    break
                location = response.headers.get("location", "").strip()
                if redirect_count >= self.max_redirects or not location:
                    return SourceVerdict(
                        False,
                        current_url,
                        "redirect",
                        "detail page exceeded redirect limit",
                        "redirect_limit",
                    )
                next_url = urljoin(str(response.url), location)
                next_verdict = self.verify_url(next_url)
                same_family = self._same_trusted_host_family(verdict.normalized_url, next_url)
                if not next_verdict.trusted:
                    if same_family and next_verdict.failure_code == "not_job_detail":
                        return next_verdict
                    return SourceVerdict(
                        False,
                        next_url,
                        next_verdict.source_type,
                        "detail page redirected to an untrusted URL",
                        "redirect_untrusted",
                    )
                if not same_family:
                    return SourceVerdict(
                        False,
                        next_url,
                        next_verdict.source_type,
                        "detail page redirected to an untrusted URL",
                        "redirect_untrusted",
                    )
                current_url = next_url

            if response is None:
                raise RuntimeError("detail page request did not produce a response")
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
                str(response.url),
                verdict.source_type,
                verdict.reason,
                verdict.failure_code,
                hashlib.sha256(page_text.encode("utf-8")).hexdigest(),
                page_text,
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
