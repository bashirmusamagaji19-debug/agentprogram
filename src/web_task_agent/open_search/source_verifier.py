from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class SourceVerdict:
    trusted: bool
    normalized_url: str
    source_type: str
    reason: str
    failure_code: str | None = None


class SourceVerifier:
    _ats = ("greenhouse.io", "lever.co", "myworkdayjobs.com", "ashbyhq.com")
    _search_hosts = ("google.", "bing.com", "baidu.com", "duckduckgo.com")

    def verify_url(self, url: str) -> SourceVerdict:
        normalized = url.strip()
        parsed = urlparse(normalized)
        host = parsed.netloc.casefold().split(":", 1)[0]
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
