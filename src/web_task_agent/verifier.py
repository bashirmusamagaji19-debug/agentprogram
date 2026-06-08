from __future__ import annotations

from dataclasses import dataclass

from web_task_agent.models import JobPosting


@dataclass(frozen=True)
class VerificationResult:
    is_valid: bool
    reasons: list[str]


class JobVerifier:
    def __init__(
        self,
        required_keywords: list[str] | None = None,
        min_confidence: float = 0.5,
    ):
        self.required_keywords = [
            keyword.lower()
            for keyword in (required_keywords or ["AI", "LLM", "Agent"])
            if keyword.strip()
        ]
        self.min_confidence = min_confidence

    def verify(self, job: JobPosting) -> VerificationResult:
        reasons: list[str] = []
        if job.confidence < self.min_confidence:
            reasons.append(f"confidence below {self.min_confidence:g}")
        if not job.requirements.strip() and not job.responsibilities.strip():
            reasons.append("missing requirements and responsibilities")
        if not self._is_relevant(job):
            reasons.append("not relevant to AI internship direction")
        return VerificationResult(is_valid=not reasons, reasons=reasons)

    def dedupe(
        self,
        jobs: list[JobPosting],
    ) -> tuple[list[JobPosting], list[JobPosting]]:
        seen_urls: set[str] = set()
        seen_company_titles: set[tuple[str, str]] = set()
        unique: list[JobPosting] = []
        duplicates: list[JobPosting] = []

        for job in jobs:
            url_key = job.url.strip().lower()
            title_key = (
                job.company.strip().lower(),
                job.title.strip().lower(),
            )
            if url_key in seen_urls or title_key in seen_company_titles:
                duplicates.append(job)
                continue

            seen_urls.add(url_key)
            seen_company_titles.add(title_key)
            unique.append(job)

        return unique, duplicates

    def _is_relevant(self, job: JobPosting) -> bool:
        if not self.required_keywords:
            return True
        haystack = " ".join(
            [
                job.title,
                job.requirements,
                job.responsibilities,
                " ".join(job.skills),
            ]
        ).lower()
        return any(keyword in haystack for keyword in self.required_keywords)
