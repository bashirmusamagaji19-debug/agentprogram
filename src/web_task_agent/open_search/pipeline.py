from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .artifacts import ArtifactWriter
from .detail_extractor import DetailExtractionError, extract_verified_job
from .evidence import build_content_hash
from .models import FailureRecord, SearchIntent, SearchRunSummary, VerifiedJob
from .source_verifier import SourceVerifier


class OpenSearchPipeline:
    def __init__(
        self,
        provider,
        *,
        source_verifier: SourceVerifier | None = None,
        verify_reachability: bool = False,
    ) -> None:
        self.provider = provider
        self.source_verifier = source_verifier or SourceVerifier()
        self.verify_reachability = verify_reachability

    async def run(
        self,
        intent: SearchIntent,
        *,
        output_dir: Path,
        limit: int | None = None,
        run_id: str | None = None,
    ):
        run_id = run_id or str(uuid4())
        writer = ArtifactWriter(output_dir)
        failures: list[FailureRecord] = []
        jobs: list[VerifiedJob] = []
        target = limit or intent.target_count
        try:
            candidates = await self.provider.search(intent.raw_text, limit=min(target * 2, 20))
        except Exception as exc:  # provider boundary: classify and preserve failure
            failures.append(FailureRecord(code="search_api_error", message=type(exc).__name__))
            summary = SearchRunSummary(
                run_id=run_id,
                target_count=target,
                candidates_seen=0,
                verified_count=0,
                failures=1,
                terminal_reason="search_api_error",
                finished_at=datetime.now(UTC),
            )
            writer.write_json("run-summary.json", summary.model_dump(mode="json"))
            writer.append_jsonl("failures.jsonl", failures[0].model_dump(mode="json"))
            return type(
                "PipelineResult", (), {"summary": summary, "jobs": jobs, "failures": failures}
            )()
        seen: set[str] = set()
        for candidate in candidates:
            if len(jobs) >= target:
                break
            if candidate.url in seen:
                continue
            seen.add(candidate.url)
            verdict = (
                await self.source_verifier.verify_reachable(candidate.url)
                if self.verify_reachability
                else self.source_verifier.verify_url(candidate.url)
            )
            trace = {
                "url": candidate.url,
                "final_url": verdict.normalized_url,
                "trusted": verdict.trusted,
                "reachability_checked": self.verify_reachability,
                "content_hash": verdict.content_hash,
            }
            if not verdict.trusted:
                failures.append(
                    FailureRecord(
                        code=verdict.failure_code or "source_untrusted",
                        url=candidate.url,
                        message=verdict.reason,
                    )
                )
                trace["failure_code"] = verdict.failure_code or "source_untrusted"
                writer.append_jsonl("execution-trace.jsonl", trace)
                continue

            page_html = (
                verdict.page_html
                if self.verify_reachability
                else str(candidate.metadata.get("page_html", ""))
            ).strip()
            content_hash = verdict.content_hash or (
                build_content_hash(page_html) if page_html else ""
            )
            trace["content_hash"] = content_hash
            if not page_html:
                failure = FailureRecord(
                    code="extraction_incomplete",
                    url=verdict.normalized_url,
                    message="trusted candidate has no verified detail-page content",
                    stage="extract_job",
                )
                failures.append(failure)
                trace["failure_code"] = failure.code
                writer.append_jsonl("execution-trace.jsonl", trace)
                continue
            try:
                job = extract_verified_job(
                    page_html,
                    page_url=verdict.normalized_url,
                    source_type=verdict.source_type,
                    content_hash=content_hash,
                )
            except DetailExtractionError as exc:
                failures.append(
                    FailureRecord(
                        code=exc.code,
                        url=verdict.normalized_url,
                        message=str(exc),
                        stage="extract_job",
                    )
                )
                trace["failure_code"] = exc.code
                writer.append_jsonl("execution-trace.jsonl", trace)
                continue
            jobs.append(job)
            trace["extracted"] = True
            trace["extraction_method"] = job.metadata.get("extraction_method", "unknown")
            writer.append_jsonl("execution-trace.jsonl", trace)
        reason = (
            "target_reached"
            if len(jobs) >= target
            else "budget_exhausted"
            if candidates
            else "no_match"
        )
        summary = SearchRunSummary(
            run_id=run_id,
            target_count=target,
            candidates_seen=len(candidates),
            verified_count=len(jobs),
            failures=len(failures),
            terminal_reason=reason,
            finished_at=datetime.now(UTC),
            metadata={
                "provider": type(self.provider).__name__,
                "malformed_candidates": getattr(self.provider, "last_malformed_count", 0),
                "reachability_checked": self.verify_reachability,
                "final_fields_source": "detail_page",
                "extraction_methods": dict(
                    sorted(
                        Counter(
                            job.metadata.get("extraction_method", "unknown") for job in jobs
                        ).items()
                    )
                ),
                "evidence_complete_jobs": sum(
                    {"title", "company", "location"}
                    <= {evidence.field_name for evidence in job.evidence}
                    for job in jobs
                ),
            },
        )
        writer.write_json("run-summary.json", summary.model_dump(mode="json"))
        for job in jobs:
            writer.append_jsonl("jobs.jsonl", job.model_dump(mode="json"))
        for failure in failures:
            writer.append_jsonl("failures.jsonl", failure.model_dump(mode="json"))
        return type(
            "PipelineResult", (), {"summary": summary, "jobs": jobs, "failures": failures}
        )()
