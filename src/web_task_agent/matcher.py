from __future__ import annotations

from web_task_agent.models import JobPosting, MatchResult, UserProfile


class JobMatcher:
    def __init__(
        self,
        *,
        llm_matcher=None,
        llm_min_rule_score: float = 0.6,
    ) -> None:
        """Parameters:
        llm_matcher: callable or None. When set and rule score < llm_min_rule_score,
                     the matcher falls back to LLM semantic matching.
        llm_min_rule_score: rule score below which LLM fallback is attempted.
        """
        self._llm_matcher = llm_matcher
        self._llm_min_rule_score = llm_min_rule_score

    @property
    def llm_matcher(self):
        return self._llm_matcher

    def match(self, *, user: UserProfile, job: JobPosting) -> MatchResult:
        required_skills = job.skills
        if not required_skills:
            return MatchResult(
                job_id=job.url,
                score=0.0,
                priority="low",
                reason="岗位未抽取到技能要求，无法计算有效匹配度。",
                suggested_actions=["补充岗位技能要求后再评估。"],
            )

        rule_result = self._rule_match(user=user, job=job, required_skills=required_skills)

        if (
            self._llm_matcher is not None
            and rule_result.score < self._llm_min_rule_score
        ):
            try:
                llm_payload = {
                    "user_skills": ", ".join(user.skills),
                    "user_resume": user.resume_text,
                    "job_title": job.title,
                    "job_company": job.company,
                    "job_requirements": job.requirements,
                    "job_responsibilities": job.responsibilities,
                    "job_skills": ", ".join(required_skills),
                }
                llm_fields = self._llm_matcher(llm_payload)
                return MatchResult(
                    job_id=job.url,
                    score=float(llm_fields.get("score", rule_result.score)),
                    matched_skills=self._str_list(llm_fields.get("matched_skills")),
                    missing_skills=self._str_list(llm_fields.get("missing_skills")),
                    reason=str(llm_fields.get("reason", rule_result.reason)),
                    priority=str(llm_fields.get("priority", rule_result.priority)),
                    suggested_actions=self._str_list(llm_fields.get("suggested_actions")),
                )
            except Exception:
                pass  # LLM call failed → fall back to rule result silently

        return rule_result

    def _rule_match(
        self,
        *,
        user: UserProfile,
        job: JobPosting,
        required_skills: list[str],
    ) -> MatchResult:
        user_signal = self._user_signal(user)
        matched_skills = [
            skill for skill in required_skills if skill.casefold() in user_signal
        ]
        missing_skills = [
            skill for skill in required_skills if skill.casefold() not in user_signal
        ]
        score = round(len(matched_skills) / len(required_skills), 2)

        return MatchResult(
            job_id=job.url,
            score=score,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            reason=(
                f"匹配 {len(matched_skills)}/{len(required_skills)} 个岗位技能："
                f"{', '.join(matched_skills) if matched_skills else '暂无'}。"
            ),
            priority=self._priority(score),
            suggested_actions=self._suggest_actions(missing_skills),
        )

    def match_many(
        self,
        *,
        user: UserProfile,
        jobs: list[JobPosting],
    ) -> list[MatchResult]:
        return [self.match(user=user, job=job) for job in jobs]

    def _user_signal(self, user: UserProfile) -> set[str]:
        signal = {skill.casefold() for skill in user.skills}
        resume_text = user.resume_text.casefold()
        for skill in self._known_skill_terms():
            if skill.casefold() in resume_text:
                signal.add(skill.casefold())
        return signal

    def _priority(self, score: float) -> str:
        if score >= 0.75:
            return "high"
        if score >= 0.4:
            return "medium"
        return "low"

    def _suggest_actions(self, missing_skills: list[str]) -> list[str]:
        if not missing_skills:
            return ["技能匹配度较高，可以优先准备投递材料。"]
        return [f"补强或准备相关经历：{skill}" for skill in missing_skills]

    def _str_list(self, value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    def _known_skill_terms(self) -> list[str]:
        return [
            "Python",
            "LangGraph",
            "LLM",
            "FastAPI",
            "SQL",
            "RAG",
            "browser-use",
            "SQLite",
            "Pydantic",
        ]
