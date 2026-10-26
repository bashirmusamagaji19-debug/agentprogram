from __future__ import annotations

import json
import re
from collections.abc import Iterable
from time import perf_counter
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from web_task_agent.agent_models import (
    AgentAction,
    AgentDecision,
    DecisionAgentState,
    ToolObservation,
)
from web_task_agent.browser import PageTimeoutError
from web_task_agent.page_reliability import PageFailureCategory, classify_page_exception
from web_task_agent.search_conditions import job_condition_reasons
from web_task_agent.visual_extractor import job_from_visual_fields


class AgentTool(Protocol):
    name: AgentAction

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation: ...


class AgentToolRegistry:
    def __init__(self, tools: Iterable[AgentTool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    async def execute(
        self,
        decision: AgentDecision,
        state: DecisionAgentState,
    ) -> ToolObservation:
        tool = self._tools.get(decision.action)
        if tool is None:
            return ToolObservation(
                tool_name=decision.action,
                success=False,
                summary=f"No tool is registered for {decision.action.value}.",
                error_category="unregistered_tool",
                error_message=decision.action.value,
                recoverable=False,
            )

        started = perf_counter()
        try:
            observation = await tool.execute(state, decision.arguments)
        except Exception as exc:
            observation = ToolObservation(
                tool_name=decision.action,
                success=False,
                summary=f"{decision.action.value} raised an unexpected error.",
                error_category="tool_error",
                error_message=type(exc).__name__,
                recoverable=False,
            )
        latency_ms = (perf_counter() - started) * 1000
        return observation.model_copy(update={"latency_ms": latency_ms})


class SearchJobsTool:
    name = AgentAction.SEARCH_JOBS

    def __init__(self, browser) -> None:
        self.browser = browser

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation:
        query = str(arguments.get("query") or state.user.keyword).strip()
        state.search_attempted = True
        state.search_attempts += 1
        target_count = int(arguments.get("target_count") or state.user.target_count)
        try:
            pages = await self.browser.search(query, target_count=target_count)
        except Exception as exc:
            failure = classify_page_exception(exc)
            return _failed(
                self.name,
                "Search failed.",
                exc,
                category="search_error",
                recoverable=failure.category in {PageFailureCategory.TRANSIENT, PageFailureCategory.BLOCKED},
            )

        candidates: list[str] = []
        for page in pages:
            if "candidate_urls" in page.metadata:
                metadata_urls = page.metadata.get("candidate_urls", [])
                urls = metadata_urls if isinstance(metadata_urls, list) else []
            else:
                urls = [page.url]
            for url in urls:
                value = str(url).strip()
                if value and value not in candidates:
                    candidates.append(value)
        for url in candidates:
            if url not in state.candidate_urls:
                state.candidate_urls.append(url)
        state.candidate_summaries.update(getattr(self.browser, "candidate_summaries", {}))
        return ToolObservation(
            tool_name=self.name,
            success=bool(candidates),
            summary=f"Discovered {len(candidates)} candidate URLs.",
            payload={"candidate_urls": candidates, "query": query},
            error_category="" if candidates else "no_candidates",
            error_message="" if candidates else "Search returned no candidate URLs.",
            recoverable=False,
        )


class OpenPageTool:
    name = AgentAction.OPEN_PAGE

    def __init__(self, browser) -> None:
        self.browser = browser

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation:
        url = str(arguments.get("url") or state.current_url or "").strip()
        if not url:
            return _state_failure(self.name, "open_page requires a URL")
        state.current_url = url
        state.current_page = None
        state.current_job_key = None
        state.retry_counts[url] = state.retry_counts.get(url, 0) + 1
        try:
            page = await self.browser.open_url(url)
        except (TimeoutError, PageTimeoutError) as exc:
            return _failed(
                self.name,
                "Page navigation timed out.",
                exc,
                category="page_timeout",
                recoverable=True,
            )
        except Exception as exc:
            failure = classify_page_exception(exc)
            observation = _failed(
                self.name,
                "Page navigation failed.",
                exc,
                category=("browser_error" if failure.category is PageFailureCategory.UNKNOWN
                          else failure.category.value),
                recoverable=failure.category is not PageFailureCategory.REMOVED,
            )
            return observation.model_copy(update={"error_message": failure.detail})

        state.current_page = page
        state.visited_urls.add(url)
        return ToolObservation(
            tool_name=self.name,
            success=True,
            summary=f"Opened {url}.",
            payload={"url": url, "title": page.title},
        )


class ExtractTextTool:
    name = AgentAction.EXTRACT_TEXT

    def __init__(self, extractor) -> None:
        self.extractor = extractor

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation:
        if state.current_page is None:
            return _state_failure(self.name, "extract_text requires a current page")
        attempts = state.page_attempts.setdefault(state.current_url or state.current_page.url, {})
        attempts["extract_text"] = attempts.get("extract_text", 0) + 1
        try:
            job = self.extractor.extract(state.current_page)
        except Exception as exc:
            return _failed(
                self.name,
                "Text extraction failed.",
                exc,
                category="text_extraction_error",
                recoverable=state.visual_available,
            )
        _replace_job_for_current_url(state, job)
        return ToolObservation(
            tool_name=self.name,
            success=True,
            summary=f"Extracted {job.title} with confidence {job.confidence:.2f}.",
            payload={"job": job.model_dump(mode="json"), "confidence": job.confidence},
        )


class ExtractVisualTool:
    name = AgentAction.EXTRACT_VISUAL

    def __init__(self, visual_extractor) -> None:
        self.visual_extractor = visual_extractor

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation:
        if state.current_page is None:
            return _state_failure(self.name, "extract_visual requires a current page")
        if state.current_page.metadata.get("candidate_binding") == "shared_page_requires_review":
            return ToolObservation(
                tool_name=self.name, success=False, recoverable=False,
                summary="Shared page requires candidate identity review.",
                error_category="shared_page_requires_review",
            )
        attempts = state.page_attempts.setdefault(state.current_url or state.current_page.url, {})
        attempts["extract_visual"] = attempts.get("extract_visual", 0) + 1
        try:
            result = await self.visual_extractor.extract(state.current_page)
        except Exception as exc:
            return _failed(
                self.name,
                "Visual extraction failed.",
                exc,
                category="visual_extraction_error",
                recoverable=True,
            )
        if not result.success or result.fields is None:
            return ToolObservation(
                tool_name=self.name,
                success=False,
                summary="Visual extraction returned no usable fields.",
                error_category="visual_extraction_error",
                error_message=result.error or "empty visual result",
                recoverable=True,
            )
        job = job_from_visual_fields(page=state.current_page, fields=result.fields)
        _replace_job_for_current_url(state, job)
        return ToolObservation(
            tool_name=self.name,
            success=True,
            summary=f"Visually extracted {job.title} with confidence {job.confidence:.2f}.",
            payload={"job": job.model_dump(mode="json"), "confidence": job.confidence},
        )


class VerifyJobTool:
    name = AgentAction.VERIFY_JOB

    def __init__(self, verifier, *, enforce_search_conditions: bool = False,
                 link_verifier=None, require_verified_links: bool = False) -> None:
        self.verifier = verifier
        self.enforce_search_conditions = enforce_search_conditions
        self.link_verifier = link_verifier
        self.require_verified_links = require_verified_links

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation:
        if not state.extracted_jobs:
            return _state_failure(self.name, "verify_job requires an extracted job")
        job = state.extracted_jobs[-1]
        result = self.verifier.verify(job)
        reasons = list(result.reasons)
        if not result.is_valid and not reasons:
            reasons.append("verifier_rejected")
        if self.enforce_search_conditions or state.enforce_search_conditions:
            reasons.extend(job_condition_reasons(state.user.keyword, state.user.location, job))
        if (re.search(
                r"\bhr\b|human\s+resources|人力资源|招聘(?:专员|助理|实习)|"
                r"行政(?:助理|实习)|财务(?:助理|实习)",
                job.title,
                re.IGNORECASE,
            )
                    and not re.search(r"算法|开发|工程师|engineer|developer", job.title, re.IGNORECASE)):
                reasons.append("nontechnical_role")
        if reasons:
            return ToolObservation(
                tool_name=self.name,
                success=False,
                summary=f"Verifier rejected {job.title}.",
                payload={"reasons": reasons, "job": job.model_dump(mode="json")},
                error_category="verification_filtered",
                error_message="; ".join(reasons),
                recoverable=True,
            )
        if self.require_verified_links or state.require_verified_links:
            from web_task_agent.link_verification import JobLinkVerifier, is_deliverable

            checker = self.link_verifier or JobLinkVerifier()
            proof = await checker.verify(job, run_id=state.execution_id)
            job = job.model_copy(update={"link_verification": proof})
            state.extracted_jobs[-1] = job
            if not is_deliverable(job, state.execution_id):
                rejected = proof.get("status") == "rejected"
                if not rejected:
                    state.pending_jobs = [item for item in state.pending_jobs if item.url != job.url] + [job]
                state.processed_urls.add(state.current_url or job.url)
                return ToolObservation(
                    tool_name=self.name, success=False, recoverable=False,
                    summary=f"Link {'rejected' if rejected else 'pending verification'}: {job.title}.",
                    payload={"reasons": [str(proof.get('reason', 'link_unconfirmed'))],
                             "job": job.model_dump(mode="json")},
                    error_category="link_rejected" if rejected else "link_pending",
                    error_message=str(proof.get("reason", "link_unconfirmed")),
                )
        metadata = state.current_page.metadata if state.current_page is not None else {}
        keys = _job_identity_keys(job, metadata)
        existing_keys = set(state.verified_job_keys)
        for existing in state.verified_jobs:
            existing_keys.update(_job_identity_keys(existing, {}))
        if not keys.intersection(existing_keys):
            state.verified_jobs.append(job)
            state.matches = []
            state.saved = False
        else:
            for index, existing in enumerate(state.verified_jobs):
                if keys.intersection(_job_identity_keys(existing, {})):
                    locations = list(dict.fromkeys(
                        part.strip() for part in re.split(r"[/、,，;；]", existing.location + " / " + job.location)
                        if part.strip()))
                    merged = " / ".join(locations)
                    if merged != existing.location:
                        state.verified_jobs[index] = existing.model_copy(update={"location": merged})
                        state.matches = []
                        state.saved = False
                    break
        state.verified_job_keys.update(keys)
        state.processed_urls.add(state.current_url or job.url)
        return ToolObservation(
            tool_name=self.name,
            success=True,
            summary=f"Verifier accepted {job.title}.",
            payload={"job": job.model_dump(mode="json")},
        )


class ScoreMatchTool:
    name = AgentAction.SCORE_MATCH

    def __init__(self, matcher) -> None:
        self.matcher = matcher

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation:
        if not state.verified_jobs:
            return _state_failure(self.name, "score_match requires verified jobs")
        state.matches = self.matcher.match_many(user=state.user, jobs=state.verified_jobs)
        return ToolObservation(
            tool_name=self.name,
            success=True,
            summary=f"Scored {len(state.matches)} verified jobs.",
            payload={"matches": [match.model_dump(mode="json") for match in state.matches]},
        )


class SaveResultsTool:
    name = AgentAction.SAVE_RESULTS

    def __init__(self, repository, *, require_verified_links: bool = False) -> None:
        self.repository = repository
        self.require_verified_links = require_verified_links

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation:
        if self.require_verified_links or state.require_verified_links:
            from web_task_agent.link_verification import is_deliverable

            blocked = [job for job in state.verified_jobs if not is_deliverable(job, state.execution_id)]
            if blocked:
                for job in blocked:
                    pending = job.model_copy(update={"link_verification": {
                        **job.link_verification, "status": "pending", "reason": "missing_or_expired_link_receipt"}})
                    state.pending_jobs = [item for item in state.pending_jobs if item.url != job.url] + [pending]
                blocked_urls = {job.url for job in blocked}
                state.verified_jobs = [job for job in state.verified_jobs if job.url not in blocked_urls]
                state.matches = []
                state.saved = False
                return ToolObservation(
                    tool_name=self.name, success=False, recoverable=False,
                    summary="Save blocked: link verification is missing or expired; run a fresh search.",
                    error_category="link_verification_required",
                    payload={"blocked_urls": sorted(blocked_urls)},
                )
        try:
            key = str(arguments.get("approval_id") or f"auto:{state.execution_id}")
            receipt = self.repository.save_jobs_once(
                state.verified_jobs,
                idempotency_key=key,
            )
        except Exception as exc:
            return _failed(
                self.name,
                "Persisting verified jobs failed.",
                exc,
                category="storage_error",
                recoverable=False,
            )
        state.saved = True
        return ToolObservation(
            tool_name=self.name,
            success=True,
            summary=f"Persisted {receipt.saved_jobs} verified jobs.",
            payload={"saved_jobs": receipt.saved_jobs, "reused": receipt.reused},
        )


class FinishTool:
    name = AgentAction.FINISH

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation:
        reason = str(arguments.get("terminal_reason") or "finished")
        if (len(state.verified_jobs) >= state.user.target_count and state.saved
                and reason == "target_reached"):
            status = "completed"
        elif state.verified_jobs and state.saved:
            status = "partial"
        else:
            status = "failed"
        state.terminal_status = status
        state.terminal_reason = reason
        return ToolObservation(
            tool_name=self.name,
            success=True,
            summary=f"Agent finished with status {status}: {reason}.",
            payload={"terminal_status": status, "terminal_reason": reason},
        )


def _replace_job_for_current_url(state: DecisionAgentState, job) -> None:
    state.extracted_jobs = [
        existing for existing in state.extracted_jobs if existing.url != job.url
    ]
    state.extracted_jobs.append(job)
    state.current_job_key = job.url


def _job_identity_keys(job, metadata: dict[str, Any]) -> set[str]:
    """Keep meaningful URL IDs; discard only known advertising parameters."""
    parts = urlsplit(str(metadata.get("canonical_url") or job.url))
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not key.casefold().startswith("utm_") and key.casefold() not in {"gclid", "fbclid"}]
    canonical = urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(),
                           parts.path.rstrip("/"), urlencode(sorted(query)), ""))
    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().casefold()
    keys = {json.dumps(["url", canonical]),
            json.dumps([canonical, normalize(job.company), normalize(job.title), normalize(job.location)])}
    source_id = metadata.get("source_job_id")
    if source_id:
        keys.add(json.dumps([metadata.get("source_spec", job.source), str(source_id)]))
    return keys


def _state_failure(action: AgentAction, message: str) -> ToolObservation:
    return ToolObservation(
        tool_name=action,
        success=False,
        summary=message,
        error_category="invalid_state",
        error_message=message,
        recoverable=False,
    )


def _failed(
    action: AgentAction,
    summary: str,
    exc: Exception,
    *,
    category: str,
    recoverable: bool,
) -> ToolObservation:
    return ToolObservation(
        tool_name=action,
        success=False,
        summary=summary,
        error_category=category,
        error_message=type(exc).__name__,
        recoverable=recoverable,
    )
