from __future__ import annotations

from web_task_agent.agent_models import (
    AgentAction,
    AgentDecision,
    DecisionAgentState,
    DecisionSource,
)


class DeterministicAgentPolicy:
    # 输入当前运行状态，返回一条带理由的 AgentDecision。分支顺序就是动作优先级。
    # 例如达到目标后先评分和保存，队列里还有链接时也可以停止发现。
    def __init__(
        self,
        *,
        text_confidence_threshold: float = 0.6,
        max_url_attempts: int = 2,
        max_search_attempts: int = 2,
    ) -> None:
        # 文本质量阈值用于选择视觉补救。两个尝试上限分别管搜索和每个 URL 的打开次数，
        # 首次调用已经算一次，默认上限 2 表示同一环节通常还能再试一次。
        self.text_confidence_threshold = text_confidence_threshold
        self.max_url_attempts = max_url_attempts
        self.max_search_attempts = max_search_attempts

    def decide(self, state: DecisionAgentState) -> AgentDecision:
        # 完成交付优先于预算耗尽。上一步保存若恰好用完预算，仍可正常报告达成目标。
        if len(state.verified_jobs) >= state.user.target_count and state.saved and state.matches:
            return self._decision(
                AgentAction.FINISH, "The requested results have been persisted.",
                arguments={"terminal_reason": "target_reached"},
            )
        # 已无工具步数时仍可调用 FINISH，记录预算耗尽以及是否已有部分结果可交付。
        if state.budget.exhausted:
            return self._decision(
                AgentAction.FINISH,
                "The execution step budget is exhausted.",
                arguments={"terminal_reason": "budget_exhausted"},
            )

        last = state.last_observation
        # 评分或保存刚失败时跳过提前交付分支，由下面的失败分支给出真实的结束原因。
        terminal_failure = last and not last.success and last.tool_name in {
            AgentAction.SCORE_MATCH, AgentAction.SAVE_RESULTS}
        # 一页处理完成后，打开、提取、校验新候选通常还要三步。
        # 剩余空间紧张时优先交付手头的岗位，且避免重新执行刚失败的评分或保存。
        at_boundary = state.current_page is None or state.current_url in state.processed_urls
        pending_candidate = self._next_candidate(state) is not None or not at_boundary
        # 还有至少两步时先评分再保存。只剩一步且已有岗位时，可以直接保存岗位。
        # 已保存后根据是否还有候选或未评分结果区分预算用尽和候选耗尽。
        if (state.verified_jobs and not terminal_failure and
                (state.budget.remaining_steps <= 3 or (at_boundary and state.budget.remaining_steps < 6))):
            if not state.matches and state.budget.remaining_steps >= 2:
                return self._decision(AgentAction.SCORE_MATCH, "Match verified results before the step limit.")
            if not state.saved:
                return self._decision(AgentAction.SAVE_RESULTS, "Save verified results before the step limit.")
            return self._decision(AgentAction.FINISH, "Deliver verified results within the step limit.",
                                  arguments={"terminal_reason": "budget_exhausted" if pending_candidate
                                             or not state.matches else "candidates_exhausted"})
        # 按上次失败所属环节找恢复动作。能恢复就优先恢复，否则转向跳过或结束。
        if last and not last.success:
            recovery = self._recover_from_failure(state)
            if recovery is not None:
                # 若恢复动作要打开另一个候选，就结束当前候选的生命周期。
                if (state.current_url and recovery.action is AgentAction.OPEN_PAGE
                        and recovery.target != state.current_url):
                    state.processed_urls.add(state.current_url)
                    state.rejected_urls.add(state.current_url)
                return recovery
            if last.tool_name in {AgentAction.SAVE_RESULTS, AgentAction.SCORE_MATCH}:
                # 结果操作失败时把错误类别带到终态，便于报告解释为何未完成交付。
                return self._decision(
                    AgentAction.FINISH, "A terminal result operation failed.",
                    arguments={"terminal_reason": last.error_category},
                )
            if last.tool_name is AgentAction.SEARCH_JOBS and not state.verified_jobs:
                # 搜索重试已不适用时，区分浏览器搜索错误和本轮没有发现候选。
                return self._decision(
                    AgentAction.FINISH, "Search returned no usable candidates.",
                    arguments={"terminal_reason": "search_error" if last.error_category == "search_error"
                               else "no_candidates"},
                )
            if state.current_url and last.tool_name is not AgentAction.SEARCH_JOBS:
                state.processed_urls.add(state.current_url)
                state.rejected_urls.add(state.current_url)

        # 达到的是通过校验并去重后的岗位数。补齐评分与持久化后再结束。
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

        # 文本工具成功表示抽到了结构化字段。置信度低时仍可先试视觉，再决定是否收下。
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

        # 当前页为空或已处理完时才切换候选。刚打开但仍待提取的页面会继续留在当前状态。
        next_url = self._next_candidate(state)
        if (state.current_page is None or state.current_url in state.processed_urls) and next_url is not None:
            return self._decision(
                AgentAction.OPEN_PAGE,
                "Open the next unvisited candidate job URL.",
                target=next_url,
            )

        # 已经提供种子 URL 的运行可以直接打开候选。首次没有任何候选时才主动发起搜索。
        if not state.candidate_urls and state.current_page is None and not state.search_attempted:
            return self._decision(
                AgentAction.SEARCH_JOBS,
                "No candidate URLs are available, so job search must run first.",
                arguments={
                    "query": f"{state.user.keyword} {state.user.location}".strip(),
                    "target_count": state.user.target_count,
                },
            )

        # 用页面 URL 或提取后的岗位键确认当前页已有结果，兼容两者地址不同的情况。
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

        # 提取阶段完成后校验最新岗位。先检查当前候选是否已经处理，避免反复校验同一页。
        if (state.extracted_jobs
                and (state.current_url or state.current_job_key or state.extracted_jobs[-1].url)
                not in state.processed_urls):
            return self._decision(
                AgentAction.VERIFY_JOB,
                "Extracted job candidates require quality verification.",
                target=state.extracted_jobs[-1].url,
            )

        # 校验淘汰了首批候选时，允许搜索适配器从上次游标补充下一批。
        latest_search = next((observation for observation in reversed(state.observation_history)
                              if observation.tool_name is AgentAction.SEARCH_JOBS), None)
        search_has_more = (latest_search is None or
                           latest_search.payload.get("search_diagnostics", {}).get("has_more", True))
        if (state.search_attempted and search_has_more and state.search_attempts < self.max_search_attempts
                and len(state.verified_jobs) < state.user.target_count
                and state.budget.remaining_steps >= 6):
            return self._decision(
                AgentAction.SEARCH_JOBS, "Continue discovery after the candidate batch was exhausted.",
                arguments={"query": f"{state.user.keyword} {state.user.location}".strip(),
                           "target_count": state.user.target_count - len(state.verified_jobs)},
            )

        # 找不到更多候选时，仍可评分并保存已通过的部分结果。
        if state.verified_jobs and not state.matches:
            return self._decision(
                AgentAction.SCORE_MATCH,
                "Verified jobs are ready for profile matching.",
            )

        # 匹配结果可能已经算好，或者已有岗位可先交付。saved 用来避免重复安排保存。
        if (state.matches or state.verified_jobs) and not state.saved:
            return self._decision(
                AgentAction.SAVE_RESULTS,
                "The current verified results have not been persisted.",
            )

        # 没有可恢复、可处理或可保存的工作时，按是否曾拿到候选给出收尾原因。
        return self._decision(
            AgentAction.FINISH,
            "No further recoverable or productive action remains.",
            arguments={"terminal_reason": "candidates_exhausted" if state.candidate_urls else "no_candidates"},
        )

    def _recover_from_failure(self, state: DecisionAgentState) -> AgentDecision | None:
        # 恢复依据最近的工具观察。搜索、导航、提取和校验分别有不同的重试条件。
        last = state.last_observation
        if last is None:
            return None
        # 搜索只对标记可恢复的 search_error 重试，沿用原搜索条件并检查搜索尝试上限。
        if (last.tool_name is AgentAction.SEARCH_JOBS and last.error_category == "search_error"
                and last.recoverable and state.search_attempts < self.max_search_attempts):
            return self._decision(
                AgentAction.SEARCH_JOBS, "Retry a transient search error within the search budget.",
                arguments={"query": f"{state.user.keyword} {state.user.location}".strip(),
                           "target_count": state.user.target_count},
            )

        if last.tool_name is AgentAction.OPEN_PAGE:
            # 先尝试恢复原地址。额度耗尽或故障不可恢复时，按候选顺序选择另一页。
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
            # 同一候选的文本提取失败后，可以尝试一次尚未用过的视觉提取。
            if (last.tool_name is AgentAction.EXTRACT_TEXT and state.visual_available
                    and not state.page_attempts.get(state.current_url or "", {}).get("extract_visual")):
                return self._decision(
                    AgentAction.EXTRACT_VISUAL,
                    "Text extraction failed and a visual recovery tool is available.",
                    target=state.current_url,
                )
            # 视觉已经用过、未配置或本次视觉也失败时，寻找能继续处理的新页面。
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
            # 链接待核验或已拒绝涉及链接证据，换一种字段提取方式无法解决。
            if last.error_category in {"link_pending", "link_rejected"}:
                return None
            attempts = state.page_attempts.get(state.current_url or "", {})
            # 通过本页计数或历史观察确认已尝试过文本提取，然后判断是否还有视觉补救机会。
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
        # 普通选队列只找未成功打开且未处理的候选。对当前页重试由恢复分支单独负责。
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
        # 把分支的动作、理由、目标和参数装进统一对象，标记其来源为确定性策略。
        # Runtime 随后决定直接采用、交给 Planner 比较，或在模型失败时用它回退。
        return AgentDecision(
            action=action,
            reason=reason,
            target=target,
            arguments=arguments or {},
            source=DecisionSource.POLICY,
        )
