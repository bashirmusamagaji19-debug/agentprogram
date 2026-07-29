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
    ) -> None:
        self.text_confidence_threshold = text_confidence_threshold
        self.max_url_attempts = max_url_attempts

    def decide(self, state: DecisionAgentState) -> AgentDecision:
        if len(state.verified_jobs) >= state.user.target_count:
            return self._decision(
                AgentAction.FINISH,
                "The requested number of verified jobs has been reached.",
                arguments={"terminal_reason": "target_reached"},
            )

        if state.budget.exhausted:
            return self._decision(
                AgentAction.FINISH,
                "The execution step budget is exhausted.",
                arguments={"terminal_reason": "budget_exhausted"},
            )

        last = state.last_observation
        if last and not last.success:
            recovery = self._recover_from_failure(state)
            if recovery is not None:
                return recovery

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
        if state.current_page is None and next_url is not None:
            return self._decision(
                AgentAction.OPEN_PAGE,
                "Open the next unvisited candidate job URL.",
                target=next_url,
            )

        if not state.candidate_urls and state.current_page is None:
            return self._decision(
                AgentAction.SEARCH_JOBS,
                "No candidate URLs are available, so job search must run first.",
                arguments={
                    "query": f"{state.user.keyword} {state.user.location}".strip(),
                    "target_count": state.user.target_count,
                },
            )

        current_page_extracted = state.current_page is not None and any(
            job.url == state.current_page.url for job in state.extracted_jobs
        )
        if state.current_page is not None and not current_page_extracted:
            return self._decision(
                AgentAction.EXTRACT_TEXT,
                "The current page has not been converted into a structured job.",
                target=state.current_page.url,
            )

        if state.extracted_jobs and not state.verified_jobs:
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
            arguments={"terminal_reason": "no_action_available"},
        )

    def _recover_from_failure(self, state: DecisionAgentState) -> AgentDecision | None:
        last = state.last_observation
        if last is None:
            return None

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
            if last.tool_name is AgentAction.EXTRACT_TEXT and state.visual_available:
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
            used_text = any(
                observation.tool_name is AgentAction.EXTRACT_TEXT
                for observation in state.observation_history
            )
            used_visual = any(
                observation.tool_name is AgentAction.EXTRACT_VISUAL
                for observation in state.observation_history
            )
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

    @staticmethod
    def _next_candidate(
        state: DecisionAgentState,
        *,
        exclude: set[str] | None = None,
    ) -> str | None:
        excluded = exclude or set()
        for url in state.candidate_urls:
            if url not in excluded and url not in state.visited_urls:
                return url
        for url in state.candidate_urls:
            if url not in excluded and state.retry_counts.get(url, 0) < 2:
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
