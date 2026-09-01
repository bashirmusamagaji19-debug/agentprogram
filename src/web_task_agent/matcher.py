from __future__ import annotations

import re
import unicodedata

from web_task_agent.models import JobPosting, MatchResult, UserProfile
from web_task_agent.skill_aliases import normalize_skill, skill_variants, term_in_text


class JobMatcher:
    def __init__(
        self,
        *,
        llm_matcher=None,
        llm_min_rule_score: float = 0.6,
        llm_discount: float = 0.55,
    ) -> None:
        """Parameters:
        llm_matcher: callable or None. When set and rule score < llm_min_rule_score,
                     the matcher falls back to LLM semantic matching.
        llm_min_rule_score: rule score below which LLM fallback is attempted.
        llm_discount: LLM 乐观偏差治理——规则低分时 LLM 分数的折减系数（#17/#24）。
                      LLM 匹配器在"方向不匹配但技能词有交集"的岗位上系统性抬分
                      （标注集 #8 0.45/#12 0.65/#14 0.85），折减后混合准确率
                      0.875→0.94（与纯规则持平，实测见
                      docs/results/matcher-evaluation/），语义边界召回（#5）保留。
        """
        self._llm_matcher = llm_matcher
        self._llm_min_rule_score = llm_min_rule_score
        self._llm_discount = llm_discount

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
                # 乐观偏差治理（#24）：规则低分说明关键词证据不足，
                # LLM 高分折减后采用——防止"会 Python"就在无关岗位上抬分。
                # 下限取规则分（#27）：规则分是真实关键词证据，折减只压 LLM 的
                # 抬分部分——规则 [0.4,0.6) 的岗位不得被折减翻成 no_match。
                # LLM 未返回 score 时沿用规则分，不做折减也不加标注
                # （守卫与取值用同一默认值，#27 修复前两处默认不一致）。
                raw_llm_score = float(llm_fields.get("score", rule_result.score))
                llm_score = max(
                    rule_result.score, round(raw_llm_score * self._llm_discount, 2)
                )
                llm_reason = str(llm_fields.get("reason", rule_result.reason))
                if llm_score < raw_llm_score:
                    llm_reason = (
                        f"{llm_reason}（规则关键词命中不足，LLM 分数已折减 "
                        f"{self._llm_discount:.2f}）"
                    )
                return MatchResult(
                    job_id=job.url,
                    score=llm_score,
                    matched_skills=self._str_list(llm_fields.get("matched_skills")),
                    missing_skills=self._str_list(llm_fields.get("missing_skills")),
                    reason=llm_reason,
                    # priority 按折减后的分数重算：LLM 自报的 high 配上折减后的
                    # 低分会出现"0.40 但 priority=high"的矛盾输出（#24 实测）
                    priority=self._priority(llm_score),
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
        # 别名归一化后再交集："大模型"↔"LLM"、"检索增强"↔"RAG" 才能互相命中
        user_signal = self._user_signal(user)
        if self._skills_look_unstructured(required_skills):
            # 抽取层产出的是编号长句碎片（真实中文 JD 的主流形态），
            # 按词切分求交集必然全 0——降级为 JD 文本技能词扫描（#22）
            return self._text_scan_match(job=job, user_signal=user_signal)
        matched_skills = [
            skill
            for skill in required_skills
            if normalize_skill(skill) in user_signal
        ]
        missing_skills = [
            skill
            for skill in required_skills
            if normalize_skill(skill) not in user_signal
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

    def _skills_look_unstructured(self, required_skills: list[str]) -> bool:
        """抽取层 skills 是否为句子碎片而非技能词。

        真实中文 JD 的 requirements 是 "1、xxx；2、xxx" 编号长句，
        逗号切分产出的是句子片段——与用户技能词求交集必然全 0（#22）。
        """
        _NUMBERED = re.compile(r"^\d+\s*[、.．)）]")
        return any(
            len(skill.strip()) > 15
            or "；" in skill
            or "。" in skill
            or _NUMBERED.match(skill.strip())
            for skill in required_skills
        )

    def _text_scan_match(
        self,
        *,
        job: JobPosting,
        user_signal: set[str],
    ) -> MatchResult:
        """JD 文本技能词扫描匹配：绕过抽取层 skills 质量，直接扫正文。

        用词表+别名变体在 requirements/responsibilities 里识别技能词，
        与用户信号求交集。覆盖面受词表限制——是"方向匹配"而非逐条核对，
        语义边界仍交给 LLM 兜底。
        """
        job_text = unicodedata.normalize(
            "NFKC", f"{job.requirements}\n{job.responsibilities}"
        ).casefold()
        # 按 canonical 去重收集（同一别名组的多个变体只计一次，显示保留首个变体）
        scanned_by_canonical: dict[str, str] = {}
        for term in self._known_skill_terms() + skill_variants():
            key = normalize_skill(term)
            if key not in scanned_by_canonical and term_in_text(term, job_text):
                scanned_by_canonical[key] = term
        if not scanned_by_canonical:
            return MatchResult(
                job_id=job.url,
                score=0.0,
                priority="low",
                reason="岗位正文未能识别出技能词（词表覆盖不足），建议人工查看。",
                suggested_actions=["技能词表未覆盖该岗位方向，人工评估。"],
            )
        matched = [
            term
            for key, term in scanned_by_canonical.items()
            if key in user_signal
        ]
        missing = [
            term
            for key, term in scanned_by_canonical.items()
            if key not in user_signal
        ]
        score = round(len(matched) / len(scanned_by_canonical), 2)
        return MatchResult(
            job_id=job.url,
            score=score,
            matched_skills=matched,
            missing_skills=missing,
            reason=(
                f"JD 文本扫描匹配 {len(matched)}/{len(scanned_by_canonical)} 个识别技能："
                f"{', '.join(matched) if matched else '暂无'}。"
            ),
            priority=self._priority(score),
            suggested_actions=self._suggest_actions(missing),
        )

    def _user_signal(self, user: UserProfile) -> set[str]:
        signal = {normalize_skill(skill) for skill in user.skills}
        resume_text = unicodedata.normalize("NFKC", user.resume_text).casefold()
        # 已知词表 + 别名变体双重扫描：简历出现任一别名即视为具备该技能
        # （如简历写"检索增强"而岗位要求 RAG，双方都归一为 "rag"）。
        # ASCII 缩写走字母数字边界，避免 HTML/YAML 误命中 "ml"
        for term in self._known_skill_terms() + skill_variants():
            if term_in_text(term, resume_text):
                signal.add(normalize_skill(term))
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
        # 中文技能词（子串扫描用户简历）；别名变体由 skill_aliases 统一归一
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
            "Playwright",
            # 中文
            "大模型",
            "大语言模型",
            "自然语言处理",
            "计算机视觉",
            "机器学习",
            "深度学习",
            "强化学习",
            "多模态",
            "智能体",
            "检索增强",
            "爬虫",
            "数据挖掘",
            "数据分析",
            "预训练",
            "PyTorch",
            "TensorFlow",
        ]
