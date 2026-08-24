"""Domain models and services for open-web job search."""

from .models import (
    FailureRecord,
    FieldEvidence,
    SearchCandidate,
    SearchIntent,
    SearchRunSummary,
    VerifiedJob,
)

__all__ = [
    "FailureRecord",
    "FieldEvidence",
    "SearchCandidate",
    "SearchIntent",
    "SearchRunSummary",
    "VerifiedJob",
]
