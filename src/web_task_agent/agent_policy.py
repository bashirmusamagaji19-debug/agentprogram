from __future__ import annotations

from web_task_agent.agent_models import (
    AgentAction,
    AgentDecision,
    DecisionAgentState,
    DecisionSource,
)


class DeterministicAgentPolicy:
    def __init__(
        self,
        *,
        text_confidence_threshold: float = 0.6,
        max_url_attempts: int = 2,
        max_search_attempts: int = 2,
    ) -> None:
        self.text_confidence_threshold = text_confidence_threshold
        self.max_url_attempts = max_url_attempts
        self.max_search_attempts = max_search_attempts

    def decide(self, state: DecisionAgentState) -> AgentDecision:
        if len(state.verified_jobs) >= state.user.target_count and state.saved and state.matches:
            return self._decision(
                AgentAction.FINISH, "The requested results have been persisted.",
                arguments={"terminal_reason": "target_reached"},
            )
        if state.budget.exhausted:
            return self._decision(
                AgentAction.FINISH,
                "The execution step budget is exhausted.",
                arguments={"terminal_reason": "budget_exhausted"},
            )

        last = state.last_observation
        # Stop discovery while matching/persistence still fit in the hard step
        # limit. FINISH is free; reserve one extra step for a truthful exit.
        terminal_failure = last and not last.success and last.tool_name in {
            AgentAction.SCORE_MATCH, AgentAction.SAVE_RESULTS}
        at_boundary = state.current_page is None or state.current_url in state.processed_urls
        pending_candidate = self._next_candidate(state) is not None or not at_boundary
        if (state.verified_jobs and not terminal_failure and
                (state.budget.remaining_steps <= 3 or (at_boundary and state.budget.remaining_steps < 6))):
            if not state.matches and state.budget.remaining_steps >= 2:
                return self._decision(AgentAction.SCORE_MATCH, "Match verified results before the step limit.")
            if not state.saved:
                return self._decision(AgentAction.SAVE_RESULTS, "Save verified results before the step limit.")
            return self._decision(AgentAction.FINISH, "Deliver verified results within the step limit.",
                                  arguments={"terminal_reason": "budget_exhausted" if pending_candidate
                                             or not state.matches else "candidates_exhausted"})
        if last and not last.success:
            recovery = self._recover_from_failure(state)
            if recovery is not None:
                if (state.current_url and recovery.action is AgentAction.OPEN_PAGE
                        and recovery.target != state.current_url):
                    state.processed_urls.add(state.current_url)
                    state.rejected_urls.add(state.current_url)
                return recovery
            if last.tool_name in {AgentAction.SAVE_RESULTS, AgentAction.SCORE_MATCH}:
                return self._decision(
                    AgentAction.FINISH, "A terminal result operation failed.",
                    arguments={"terminal_reason": last.error_category},
                )
            if last.tool_name is AgentAction.SEARCH_JOBS:
                return self._decision(
                    AgentAction.FINISH, "Search returned no usable candidates.",
                    arguments={"terminal_reason": "search_error" if last.error_category == "search_error"
                               else "no_candidates"},
                )
            if state.current_url:
                state.processed_urls.add(state.current_url)
                state.rejected_urls.add(state.current_url)

        target_reached = len(state.verified_jobs) >= state.user.target_count
        if target_reached and not state.matches:
            return self._decision(
                AgentAction.SCORE_MATCH,
                "The verified target is ready for profile matching.",
            )
        if target_reached and not state.saved:
            return self._decision(
                AgentAction.SAVE_RESULTS,
                "Matched target results are ready for persistence.",
            )
        if target_reached:
            return self._decision(
                AgentAction.FINISH,
                "The requested results were verified, matched, and saved.",
                arguments={"terminal_reason": "target_reached"},
            )

        if (
            last
            and last.tool_name is AgentAction.EXTRACT_TEXT
            and last.success
            and float(last.payload.get("confidence", 0.0)) < self.text_confidence_threshold
            and state.visual_available
        ):
            return self._decision(
                AgentAction.EXTRACT_VISUAL,
                (
                    "Text extraction confidence is low, so the visual extractor is the "
                    "next recovery tool."
                ),
                target=state.current_url,
            )

        next_url = self._next_candidate(state)
        if (state.current_page is None or state.current_url in state.processed_urls) and next_url is not None:
            return self._decision(
                AgentAction.OPEN_PAGE,
                "Open the next unvisited candidate job URL.",
                target=next_url,
            )

        if not state.candidate_urls and state.current_page is None and not state.search_attempted:
            return self._decision(
                AgentAction.SEARCH_JOBS,
                "No candidate URLs are available, so job search must run first.",
                arguments={
                    "query": f"{state.user.keyword} {state.user.location}".strip(),
                    "target_count": state.user.target_count,
                },
            )

        current_page_extracted = state.current_page is not None and any(
            job.url in {state.current_page.url, state.current_job_key} for job in state.extracted_jobs
        )
        if (state.current_page is not None and state.current_url not in state.processed_urls
                and not current_page_extracted):
            return self._decision(
                AgentAction.EXTRACT_TEXT,
                "The current page has not been converted into a structured job.",
                target=state.current_page.url,
            )

        if (state.extracted_jobs
                and (state.current_url or state.current_job_key or state.extracted_jobs[-1].url)
                not in state.processed_urls):
            return self._decision(
                AgentAction.VERIFY_JOB,
                "Extracted job candidates require quality verification.",
                target=state.extracted_jobs[-1].url,
            )

        if state.verified_jobs and not state.matches:
            return self._decision(
                AgentAction.SCORE_MATCH,
                "Verified jobs are ready for profile matching.",
            )

        if (state.matches or state.verified_jobs) and not state.saved:
            return self._decision(
                AgentAction.SAVE_RESULTS,
                "The current verified results have not been persisted.",
            )

        return self._decision(
            AgentAction.FINISH,
            "No further recoverable or productive action remains.",
            arguments={"terminal_reason": "candidates_exhausted" if state.candidate_urls else "no_candidates"},
        )

    def _recover_from_failure(self, state: DecisionAgentState) -> AgentDecision | None:
        last = state.last_observation
        if last is None:
            return None
        if (last.tool_name is AgentAction.SEARCH_JOBS and last.error_category == "search_error"
                and last.recoverable and state.search_attempts < self.max_search_attempts):
            return self._decision(
                AgentAction.SEARCH_JOBS, "Retry a transient search error within the search budget.",
                arguments={"query": f"{state.user.keyword} {state.user.location}".strip(),
                           "target_count": state.user.target_count},
            )

        if last.tool_name is AgentAction.OPEN_PAGE:
            current = state.current_url
            attempts = state.retry_counts.get(current or "", 0)
            if last.recoverable and current and attempts < self.max_url_attempts:
                return self._decision(
                    AgentAction.OPEN_PAGE,
                    "The page failure is recoverable and remains within the per-URL retry budget.",
                    target=current,
                )
            next_url = self._next_candidate(state, exclude={current} if current else set())
            if next_url:
                return self._decision(
                    AgentAction.OPEN_PAGE,
                    "The current URL exhausted its retries, so continue with the next candidate.",
                    target=next_url,
                )

        if last.tool_name in {AgentAction.EXTRACT_TEXT, AgentAction.EXTRACT_VISUAL}:
            if (last.tool_name is AgentAction.EXTRACT_TEXT and state.visual_available
                    and not state.page_attempts.get(state.current_url or "", {}).get("extract_visual")):
                return self._decision(
                    AgentAction.EXTRACT_VISUAL,
                    "Text extraction failed and a visual recovery tool is available.",
                    target=state.current_url,
                )
            next_url = self._next_candidate(
                state,
                exclude={state.current_url} if state.current_url else set(),
            )
            if next_url:
                return self._decision(
                    AgentAction.OPEN_PAGE,
                    "Extraction could not recover this page, so continue with the next candidate.",
                    target=next_url,
                )

        if last.tool_name is AgentAction.VERIFY_JOB:
            if last.error_category in {"link_pending", "link_rejected"}:
                return None
            attempts = state.page_attempts.get(state.current_url or "", {})
            used_text = attempts.get("extract_text", 0) > 0 or any(
                observation.tool_name is AgentAction.EXTRACT_TEXT
                and observation.payload.get("job", {}).get("url", state.current_url) == state.current_url
                for observation in state.observation_history
            )
            used_visual = attempts.get("extract_visual", 0) > 0
            if state.visual_available and used_text and not used_visual:
                return self._decision(
                    AgentAction.EXTRACT_VISUAL,
                    "Verifier rejected the text result, so retry with visual evidence.",
                    target=state.current_url,
                )
            next_url = self._next_candidate(
                state,
                exclude={state.current_url} if state.current_url else set(),
            )
            if next_url:
                return self._decision(
                    AgentAction.OPEN_PAGE,
                    (
                        "Verifier rejection cannot recover on this page, so continue with "
                        "the next candidate."
                    ),
                    target=next_url,
                )

        return None

    def _next_candidate(
        self,
        state: DecisionAgentState,
        *,
        exclude: set[str] | None = None,
    ) -> str | None:
        excluded = (exclude or set()) | state.processed_urls | state.rejected_urls
        for url in state.candidate_urls:
            if (url not in excluded and url not in state.visited_urls
                    and state.retry_counts.get(url, 0) < self.max_url_attempts):
                return url
        return None

    @staticmethod
    def _decision(
        action: AgentAction,
        reason: str,
        *,
        target: str | None = None,
        arguments: dict | None = None,
    ) -> AgentDecision:
        return AgentDecision(
            action=action,
            reason=reason,
            target=target,
            arguments=arguments or {},
            source=DecisionSource.POLICY,
        )
