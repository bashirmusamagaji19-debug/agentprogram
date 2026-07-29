from __future__ import annotations

from collections.abc import Iterable
from time import perf_counter
from typing import Any, Protocol

from web_task_agent.agent_models import (
    AgentAction,
    AgentDecision,
    DecisionAgentState,
    ToolObservation,
)
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
                error_message=f"{type(exc).__name__}: {exc}",
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
        target_count = int(arguments.get("target_count") or state.user.target_count)
        try:
            pages = await self.browser.search(query, target_count=target_count)
        except Exception as exc:
            return _failed(
                self.name,
                "Search failed.",
                exc,
                category="search_error",
                recoverable=True,
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
        return ToolObservation(
            tool_name=self.name,
            success=bool(candidates),
            summary=f"Discovered {len(candidates)} candidate URLs.",
            payload={"candidate_urls": candidates, "query": query},
            error_category="" if candidates else "no_candidates",
            error_message="" if candidates else "Search returned no candidate URLs.",
            recoverable=not candidates,
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
        state.retry_counts[url] = state.retry_counts.get(url, 0) + 1
        try:
            page = await self.browser.open_url(url)
        except TimeoutError as exc:
            return _failed(
                self.name,
                "Page navigation timed out.",
                exc,
                category="page_timeout",
                recoverable=True,
            )
        except Exception as exc:
            return _failed(
                self.name,
                "Page navigation failed.",
                exc,
                category="browser_error",
                recoverable=True,
            )

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

    def __init__(self, verifier) -> None:
        self.verifier = verifier

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation:
        if not state.extracted_jobs:
            return _state_failure(self.name, "verify_job requires an extracted job")
        job = state.extracted_jobs[-1]
        result = self.verifier.verify(job)
        if not result.is_valid:
            return ToolObservation(
                tool_name=self.name,
                success=False,
                summary=f"Verifier rejected {job.title}.",
                payload={"reasons": result.reasons, "job": job.model_dump(mode="json")},
                error_category="verification_filtered",
                error_message="; ".join(result.reasons),
                recoverable=True,
            )
        if all(existing.url != job.url for existing in state.verified_jobs):
            state.verified_jobs.append(job)
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

    def __init__(self, repository) -> None:
        self.repository = repository

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation:
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
        if state.verified_jobs:
            status = "completed"
        elif state.extracted_jobs or state.candidate_urls:
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
        error_message=f"{type(exc).__name__}: {exc}",
        recoverable=recoverable,
    )
