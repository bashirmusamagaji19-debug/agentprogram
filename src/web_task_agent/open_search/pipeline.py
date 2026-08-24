from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .artifacts import ArtifactWriter
from .evidence import build_field_evidence
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

    async def run(self, intent: SearchIntent, *, output_dir: Path, limit: int | None = None):
        run_id = str(uuid4())
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
            writer.append_jsonl(
                "execution-trace.jsonl",
                {
                    "url": candidate.url,
                    "trusted": verdict.trusted,
                    "reachability_checked": self.verify_reachability,
                },
            )
            if not verdict.trusted:
                failures.append(
                    FailureRecord(
                        code=verdict.failure_code or "source_untrusted",
                        url=candidate.url,
                        message=verdict.reason,
                    )
                )
                continue
            evidence = [
                build_field_evidence(
                    "title",
                    candidate.title,
                    source_text=candidate.snippet or candidate.title,
                    page_url=candidate.url,
                )
            ]
            jobs.append(
                VerifiedJob(
                    title=candidate.title or "未命名岗位",
                    company=candidate.source or "未知公司",
                    location="未声明",
                    url=candidate.url,
                    source=verdict.source_type,
                    evidence=evidence,
                )
            )
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
        )
        writer.write_json("run-summary.json", summary.model_dump(mode="json"))
        for job in jobs:
            writer.append_jsonl("jobs.jsonl", job.model_dump(mode="json"))
        for failure in failures:
            writer.append_jsonl("failures.jsonl", failure.model_dump(mode="json"))
        return type(
            "PipelineResult", (), {"summary": summary, "jobs": jobs, "failures": failures}
        )()
