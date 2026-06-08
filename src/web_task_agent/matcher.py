from __future__ import annotations

from web_task_agent.models import JobPosting, MatchResult, UserProfile


class JobMatcher:
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
