"""Explicit link-verifier double for orchestration tests with offline pages.

This does not disable the production gate. Tests for live-page semantics use
JobLinkVerifier with separate HTML/browser fixtures instead.
"""
from datetime import UTC, datetime


class VerifiedLinkStub:
    async def verify(self, job, *, run_id):
        return {
            "status": "verified", "checked_at": datetime.now(UTC).isoformat(),
            "run_id": run_id, "requested_url": job.url, "final_url": job.url,
            "method": "browser", "reason": "explicit offline orchestration fixture",
            "evidence_sha256": "a" * 64, "title_matched": True, "jd_matched": True, "active": True,
        }
