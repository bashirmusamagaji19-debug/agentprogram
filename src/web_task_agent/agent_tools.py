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
    # name 对应 AgentDecision.action，注册表据此把决策分发给具体工具。
    # state 是本次运行共享的状态，arguments 只携带当前动作的参数。
    # 工具直接更新 state，同时返回 ToolObservation 供策略决定下一步。
    name: AgentAction

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation: ...


class AgentToolRegistry:
    # 将 AgentDecision 中的动作名称连接到具体工具。注册表负责查找、调用和计时，
    # 策略负责选动作，工具自身负责业务状态与可预期的失败。
    def __init__(self, tools: Iterable[AgentTool]) -> None:
        # 例如 OpenPageTool.name 为 OPEN_PAGE，收到同名决策时即可从字典取出这个实例。
        self._tools = {tool.name: tool for tool in tools}

    async def execute(
        self,
        decision: AgentDecision,
        state: DecisionAgentState,
    ) -> ToolObservation:
        tool = self._tools.get(decision.action)
        if tool is None:
            # 某些动作按配置注册，例如未启用视觉提取时没有对应的工具实例。
            return ToolObservation(
                tool_name=decision.action,
                success=False,
                summary=f"No tool is registered for {decision.action.value}.",
                error_category="unregistered_tool",
                error_message=decision.action.value,
                recoverable=False,
            )

        # 工具收到的是本次运行的共享状态和这一步的参数。返回后统一补工具自身的耗时。
        started = perf_counter()
        try:
            observation = await tool.execute(state, decision.arguments)
        except Exception as exc:
            # 各工具优先处理已知业务错误。这里将漏出的意外异常转成统一观察，
            # 让运行时仍能记录失败，并仅在错误信息里暴露异常类型。
            observation = ToolObservation(
                tool_name=decision.action,
                success=False,
                summary=f"{decision.action.value} raised an unexpected error.",
                error_category="tool_error",
                error_message=type(exc).__name__,
                recoverable=False,
            )
        latency_ms = (perf_counter() - started) * 1000
        # 统一记录工具执行耗时（毫秒）。observe 节点会将它计入运行指标。
        return observation.model_copy(update={"latency_ms": latency_ms})


class SearchJobsTool:
    # 将搜索词和目标数量交给浏览器或来源适配器，把发现的 URL 和摘要写入候选队列。
    # 这个动作的产出是候选线索，后续要逐个打开、提取和校验才能确定可交付数量。
    name = AgentAction.SEARCH_JOBS

    def __init__(self, browser) -> None:
        self.browser = browser

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation:
        # 优先使用决策参数。策略可以传入拼好的关键词和地点，缺省时回退到用户关键词。
        query = str(arguments.get("query") or state.user.keyword).strip()
        # 在发起浏览器调用前计数，让失败的搜索也占用策略的搜索尝试额度。
        state.search_attempted = True
        state.search_attempts += 1
        # target_count 是本次搜索请求的参数，校验通过并去重后的数量由后续动作统计。
        target_count = int(arguments.get("target_count") or state.user.target_count)
        try:
            pages = await self.browser.search(query, target_count=target_count)
        except Exception as exc:
            failure = classify_page_exception(exc)
            # 临时故障或页面拦截可供策略考虑重试，重试次数由策略控制。
            return _failed(
                self.name,
                "Search failed.",
                exc,
                category="search_error",
                recoverable=failure.category in {PageFailureCategory.TRANSIENT, PageFailureCategory.BLOCKED},
            )

        candidates: list[str] = []
        for page in pages:
            # 搜索结果页通过元数据提供详情链接，即使列表为空也按结果页处理。
            # 只有缺少这个字段时，才把返回页面自身当作岗位候选。
            if "candidate_urls" in page.metadata:
                metadata_urls = page.metadata.get("candidate_urls", [])
                urls = metadata_urls if isinstance(metadata_urls, list) else []
            else:
                urls = [page.url]
            # 本轮先去掉空链接和重复链接，保留第一次发现的顺序。
            for url in urls:
                value = str(url).strip()
                if value and value not in candidates:
                    candidates.append(value)
        # 追加从未入队的链接，保留发现顺序。候选摘要来自浏览器适配器。
        for url in candidates:
            if url not in state.candidate_urls:
                state.candidate_urls.append(url)
        state.candidate_summaries.update(getattr(self.browser, "candidate_summaries", {}))
        # 观察只报告本轮发现的候选，state.candidate_urls 保存累计队列。
        # 本轮没有候选时返回 no_candidates，和浏览器抛异常产生的 search_error 区分开。
        diagnostics = next((page.metadata["search_diagnostics"] for page in reversed(pages)
                            if "search_diagnostics" in page.metadata), getattr(self.browser, "search_diagnostics", {}))
        search_failed = diagnostics.get("stop_reason") in {"page_error", "deadline_exceeded"}
        return ToolObservation(
            tool_name=self.name,
            success=bool(candidates),
            summary=f"Discovered {len(candidates)} candidate URLs.",
            payload={"candidate_urls": candidates, "query": query, "search_diagnostics": diagnostics,
                     "shortfall": max(0, target_count - len(candidates))},
            error_category="" if candidates else "search_error" if search_failed else "no_candidates",
            error_message="" if candidates else "Search failed before discovering candidates." if search_failed
                          else "Search returned no candidate URLs.",
            recoverable=not candidates and search_failed and bool(diagnostics.get("recoverable", False)),
        )


class OpenPageTool:
    # 读取一个候选的页面，更新当前 URL、导航计数和 BrowserPage。
    # 成功只说明拿到页面，岗位内容、链接能否交付仍由下游校验。
    name = AgentAction.OPEN_PAGE

    def __init__(self, browser) -> None:
        self.browser = browser

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation:
        # 运行时会把决策目标补到 url 参数。重试时也可以沿用当前请求地址。
        url = str(arguments.get("url") or state.current_url or "").strip()
        if not url:
            return _state_failure(self.name, "open_page requires a URL")
        # 先清掉旧页面和岗位键。若导航失败，后续动作就不会误用上个候选的页面。
        state.current_url = url
        state.current_page = None
        state.current_job_key = None
        # 这里累计的是打开次数，第一次导航也算一次。
        state.retry_counts[url] = state.retry_counts.get(url, 0) + 1
        try:
            page = await self.browser.open_url(url)
        except (TimeoutError, PageTimeoutError) as exc:
            # 超时有单独的错误类别，策略可在该 URL 的尝试额度内重开。
            return _failed(
                self.name,
                "Page navigation timed out.",
                exc,
                category="page_timeout",
                recoverable=True,
            )
        except Exception as exc:
            failure = classify_page_exception(exc)
            # 保留已知故障的细分类型和详情。已下线的页面可直接换下一个候选。
            observation = _failed(
                self.name,
                "Page navigation failed.",
                exc,
                category=("browser_error" if failure.category is PageFailureCategory.UNKNOWN
                          else failure.category.value),
                recoverable=failure.category is not PageFailureCategory.REMOVED,
            )
            return observation.model_copy(update={"error_message": failure.detail})

        # 按请求地址记录导航成功。page.url 可能是重定向后的地址。
        # 这样重试、候选去重等状态仍能对上最初入队的 URL。
        # visited_urls 表示打开过，processed_urls 则表示这个候选已处理完毕。
        state.current_page = page
        state.visited_urls.add(url)
        return ToolObservation(
            tool_name=self.name,
            success=True,
            summary=f"Opened {url}.",
            payload={"url": url, "title": page.title},
        )


class ExtractTextTool:
    # 把当前页面的正文整理成 JobPosting。提取器负责生成字段，工具负责把结果写入
    # extracted_jobs，并把置信度交给策略，供它选择直接校验或视觉补救。
    name = AgentAction.EXTRACT_TEXT

    def __init__(self, extractor) -> None:
        self.extractor = extractor

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation:
        # 当前页由 OPEN_PAGE 写入。导航失败后它为空，应先由策略处理失败。
        if state.current_page is None:
            return _state_failure(self.name, "extract_text requires a current page")
        # 用候选 URL 关联本页的文本和视觉尝试，必要时回退到页面地址。
        # 策略会用这些计数判断后续还有哪种提取方式可尝试。
        attempts = state.page_attempts.setdefault(state.current_url or state.current_page.url, {})
        attempts["extract_text"] = attempts.get("extract_text", 0) + 1
        try:
            # PageExtractor 负责解析正文和计算字段完整度。配置文本模型后，
            # 规则置信度低且正文足够长时，还可尝试让模型补齐字段。
            job = self.extractor.extract(state.current_page)
        except Exception as exc:
            # 文本提取失败时，是否还有视觉补救路径取决于运行配置。
            return _failed(
                self.name,
                "Text extraction failed.",
                exc,
                category="text_extraction_error",
                recoverable=state.visual_available,
            )
        # 提取成功只表示得到了结构化岗位。策略还会读取置信度，
        # 决定先用视觉补救，还是把最新岗位交给 VerifyJobTool。
        _replace_job_for_current_url(state, job)
        # 共享状态保留岗位对象，观察里使用 JSON 友好的字段供轨迹和后续判断读取。
        return ToolObservation(
            tool_name=self.name,
            success=True,
            summary=f"Extracted {job.title} with confidence {job.confidence:.2f}.",
            payload={"job": job.model_dump(mode="json"), "confidence": job.confidence},
        )


class ExtractVisualTool:
    # 把当前页交给视觉适配器，再将返回的字段转换成通用的 JobPosting。
    # 配置了视觉适配器才会注册，策略可在文本低置信度、提取失败或部分校验失败后使用。
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
        # 多个来源候选共用一个页面时，截图可能无法确定岗位归属。
        # 先挡住身份待确认的页面，避免把共享页面内容绑定到错误的候选。
        if state.current_page.metadata.get("candidate_binding") == "shared_page_requires_review":
            return ToolObservation(
                tool_name=self.name, success=False, recoverable=False,
                summary="Shared page requires candidate identity review.",
                error_category="shared_page_requires_review",
            )
        # 通过页面身份检查后再记录尝试次数。策略可以据此避免重复尝试同一种提取方式。
        attempts = state.page_attempts.setdefault(state.current_url or state.current_page.url, {})
        attempts["extract_visual"] = attempts.get("extract_visual", 0) + 1
        try:
            # 视觉适配器负责获取并识别页面。例如 Qwen 适配器会按页面 URL 调用截图识别流程。
            result = await self.visual_extractor.extract(state.current_page)
        except Exception as exc:
            return _failed(
                self.name,
                "Visual extraction failed.",
                exc,
                category="visual_extraction_error",
                recoverable=True,
            )
        # 适配器也可用结果对象报告失败。缺少字段时没有可交给校验的岗位。
        if not result.success or result.fields is None:
            return ToolObservation(
                tool_name=self.name,
                success=False,
                summary="Visual extraction returned no usable fields.",
                error_category="visual_extraction_error",
                error_message=result.error or "empty visual result",
                recoverable=True,
            )
        # 转成与文本提取相同的 JobPosting 结构，沿用校验流程。
        # 若 job.url 相同，新提取结果会覆盖该 URL 的旧结果。
        job = job_from_visual_fields(page=state.current_page, fields=result.fields)
        _replace_job_for_current_url(state, job)
        return ToolObservation(
            tool_name=self.name,
            success=True,
            summary=f"Visually extracted {job.title} with confidence {job.confidence:.2f}.",
            payload={"job": job.model_dump(mode="json"), "confidence": job.confidence},
        )


class VerifyJobTool:
    # 将提取出的岗位推进到已校验或待核验状态。先筛内容和搜索条件，再检查链接，
    # 最后合并重复岗位。通过校验后的唯一岗位数才参与目标数量判断。
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
        # 两种提取工具都把最新 JobPosting 放在列表末尾。
        # 基础校验器负责置信度、岗位描述和 AI 方向相关性检查。
        job = state.extracted_jobs[-1]
        result = self.verifier.verify(job)
        reasons = list(result.reasons)
        # 如果底层只给出了拒绝结论，补一个通用原因，确保后续仍会进入拒绝分支。
        if not result.is_valid and not reasons:
            reasons.append("verifier_rejected")
        # 构造工具时或启动本次运行时都可启用关键词、地点约束。
        if self.enforce_search_conditions or state.enforce_search_conditions:
            reasons.extend(job_condition_reasons(state.user.keyword, state.user.location, job))
        # HR 等非技术岗位可能在描述中提到 AI，补一道标题筛选。
        # 标题同时有开发、算法或工程师等技术信号时，保留给其他规则判断。
        if (re.search(
                r"\bhr\b|human\s+resources|人力资源|招聘(?:专员|助理|实习)|"
                r"行政(?:助理|实习)|财务(?:助理|实习)",
                job.title,
                re.IGNORECASE,
            )
                    and not re.search(r"算法|开发|工程师|engineer|developer", job.title, re.IGNORECASE)):
                reasons.append("nontechnical_role")
        if reasons:
            # 回传全部拒绝原因。若这次校验的是文本结果，策略还有机会尝试视觉提取。
            return ToolObservation(
                tool_name=self.name,
                success=False,
                summary=f"Verifier rejected {job.title}.",
                payload={"reasons": reasons, "job": job.model_dump(mode="json")},
                error_category="verification_filtered",
                error_message="; ".join(reasons),
                recoverable=True,
            )
        # 内容筛查通过后才做较昂贵的链接核验。工具和本次运行任一开关为真即可启用。
        if self.require_verified_links or state.require_verified_links:
            from web_task_agent.link_verification import JobLinkVerifier, is_deliverable

            # 链接校验为本次执行生成凭据，记录实际页面与岗位身份、在招状态等证据。
            # is_deliverable 还会检查凭据是否来自浏览器、是否属于当前运行且仍在有效期内。
            checker = self.link_verifier or JobLinkVerifier()
            proof = await checker.verify(job, run_id=state.execution_id)
            # 用带凭据的版本覆盖刚提取的岗位，让后续报告和保存看到同一份核验证据。
            job = job.model_copy(update={"link_verification": proof})
            state.extracted_jobs[-1] = job
            if not is_deliverable(job, state.execution_id):
                # 明确拒绝的链接直接淘汰。证据暂缺的岗位按 URL 更新到待核验列表。
                # 两种情况都标记当前候选已处理，让策略继续推进候选队列。
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
        # 同时检查历史身份键和现有岗位重算出的键，覆盖 URL 变体和来源原生 ID。
        metadata = state.current_page.metadata if state.current_page is not None else {}
        keys = _job_identity_keys(job, metadata)
        existing_keys = set(state.verified_job_keys)
        for existing in state.verified_jobs:
            existing_keys.update(_job_identity_keys(existing, {}))
        # 没有任何身份键重合才增加一个岗位。例如仅广告参数不同的 URL 通常不会让结果数加一。
        if not keys.intersection(existing_keys):
            state.verified_jobs.append(job)
            # 已校验岗位集合变了，旧的评分和保存标记需要失效。
            state.matches = []
            state.saved = False
        else:
            # 只有能按现有记录的键找到重复项时才合并地点，按出现顺序去掉重复地点。
            for index, existing in enumerate(state.verified_jobs):
                if keys.intersection(_job_identity_keys(existing, {})):
                    locations = list(dict.fromkeys(
                        part.strip() for part in re.split(r"[/、,，;；]", existing.location + " / " + job.location)
                        if part.strip()))
                    merged = " / ".join(locations)
                    if merged != existing.location:
                        # 同一职位可合并多个地点。记录变动后，评分与保存也要以更新后的批次为准。
                        state.verified_jobs[index] = existing.model_copy(update={"location": merged})
                        state.matches = []
                        state.saved = False
                    break
        # 重复候选也可以通过校验。success 表示本次校验通过，数量以 verified_jobs 为准。
        state.verified_job_keys.update(keys)
        state.processed_urls.add(state.current_url or job.url)
        return ToolObservation(
            tool_name=self.name,
            success=True,
            summary=f"Verifier accepted {job.title}.",
            payload={"job": job.model_dump(mode="json")},
        )


class ScoreMatchTool:
    # 将用户画像与已校验岗位交给 JobMatcher，产出逐个岗位的匹配结果。
    # 匹配分反映技能契合度。提取置信度和链接能否交付已由前面的阶段处理。
    name = AgentAction.SCORE_MATCH

    def __init__(self, matcher) -> None:
        self.matcher = matcher

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation:
        # 评分依赖通过校验的岗位。有候选链接或原始提取结果时仍需先执行校验。
        if not state.verified_jobs:
            return _state_failure(self.name, "score_match requires verified jobs")
        # JobMatcher 使用画像技能和简历计算规则分。配置语义模型时，可对规则低分做补算。
        # matches 和 verified_jobs 按相同顺序对应，包含命中技能、缺失技能和行动建议。
        state.matches = self.matcher.match_many(user=state.user, jobs=state.verified_jobs)
        return ToolObservation(
            tool_name=self.name,
            success=True,
            summary=f"Scored {len(state.matches)} verified jobs.",
            payload={"matches": [match.model_dump(mode="json") for match in state.matches]},
        )


class SaveResultsTool:
    # 保存已校验的岗位批次。严格模式先复核链接，再把稳定的保存键交给仓储。
    # 匹配评分留在运行状态供报告使用，仓储调用只接收岗位列表。
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

            # 评分或人工审批期间，链接凭据可能过期，所以入库前再检查一次。
            blocked = [job for job in state.verified_jobs if not is_deliverable(job, state.execution_id)]
            if blocked:
                # 有任一凭据不可用就中止本次写入。将对应岗位移回待核验列表，
                # 清掉基于原集合计算的评分，同时重置保存标记。
                for job in blocked:
                    # 保留已收集的证据信息并标记为待确认，同 URL 的旧待核验项会被替换。
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
            # 人工审批流程会传入 approval_id。自动保存用执行 ID 生成稳定的键。
            # 仓储在同一事务中写岗位和保存回执，重放同一个键会复用已有回执。
            key = str(arguments.get("approval_id") or f"auto:{state.execution_id}")
            receipt = self.repository.save_jobs_once(
                state.verified_jobs,
                idempotency_key=key,
            )
        except Exception as exc:
            # 仓储异常时报告 storage_error，策略据此结束本次交付。
            return _failed(
                self.name,
                "Persisting verified jobs failed.",
                exc,
                category="storage_error",
                recoverable=False,
            )
        # 拿到回执才标记已保存。reused 表明本次是否复用了之前的写入结果。
        state.saved = True
        return ToolObservation(
            tool_name=self.name,
            success=True,
            summary=f"Persisted {receipt.saved_jobs} verified jobs.",
            payload={"saved_jobs": receipt.saved_jobs, "reused": receipt.reused},
        )


class FinishTool:
    # 将策略给出的结束原因和已交付数量组合成任务终态，图读取终态后退出循环。
    name = AgentAction.FINISH

    async def execute(
        self,
        state: DecisionAgentState,
        arguments: dict[str, Any],
    ) -> ToolObservation:
        # terminal_reason 描述为什么结束，例如达到目标、候选耗尽或预算耗尽。
        reason = str(arguments.get("terminal_reason") or "finished")
        # 达到目标、已保存且原因明确为 target_reached，才算完整交付。
        # 例如候选耗尽时只保存了一部分岗位，任务仍可返回 partial。
        if (len(state.verified_jobs) >= state.user.target_count and state.saved
                and reason == "target_reached"):
            status = "completed"
        elif state.verified_jobs and state.saved:
            status = "partial"
        else:
            # 找到岗位但未写入时也算交付失败，避免将发现或校验成功当成结果已保存。
            status = "failed"
        state.terminal_status = status
        state.terminal_reason = reason
        # success 说明收尾动作正常执行。整次任务是否成功看 terminal_status。
        return ToolObservation(
            tool_name=self.name,
            success=True,
            summary=f"Agent finished with status {status}: {reason}.",
            payload={"terminal_status": status, "terminal_reason": reason},
        )


def _replace_job_for_current_url(state: DecisionAgentState, job) -> None:
    # 以提取后的 job.url 精确替换旧记录。最新版本放在末尾供校验读取，
    # current_job_key 则让策略知道当前页面已经得到结构化岗位。
    state.extracted_jobs = [
        existing for existing in state.extracted_jobs if existing.url != job.url
    ]
    state.extracted_jobs.append(job)
    state.current_job_key = job.url


def _job_identity_keys(job, metadata: dict[str, Any]) -> set[str]:
    """Keep meaningful URL IDs; discard only known advertising parameters."""
    # 优先使用来源提供的规范链接，清除广告参数，同时保留 jobId 之类有身份意义的参数。
    # 统一域名大小写和查询参数顺序，避免同一岗位因链接写法不同而重复。
    parts = urlsplit(str(metadata.get("canonical_url") or job.url))
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not key.casefold().startswith("utm_") and key.casefold() not in {"gclid", "fbclid"}]
    canonical = urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(),
                           parts.path.rstrip("/"), urlencode(sorted(query)), ""))
    # 组合身份键还会用到公司、标题和地点。归一化文本空白与大小写以减少格式差异。
    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().casefold()
    # 一个岗位可有多个身份键。来源原生 ID 要和来源一起使用，以免跨站点撞号。
    keys = {json.dumps(["url", canonical]),
            json.dumps([canonical, normalize(job.company), normalize(job.title), normalize(job.location)])}
    source_id = metadata.get("source_job_id")
    if source_id:
        keys.add(json.dumps([metadata.get("source_spec", job.source), str(source_id)]))
    return keys


def _state_failure(action: AgentAction, message: str) -> ToolObservation:
    # 缺少执行前置状态时，统一返回 invalid_state，例如还没打开页面就开始提取。
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
    # 业务错误类别和可恢复性由调用者指定。异常类型供排查，后续动作由策略选择。
    return ToolObservation(
        tool_name=action,
        success=False,
        summary=summary,
        error_category=category,
        error_message=type(exc).__name__,
        recoverable=recoverable,
    )
