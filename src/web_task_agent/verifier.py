from __future__ import annotations

import re
from dataclasses import dataclass

from web_task_agent.keywords import AI_JOB_KEYWORDS
from web_task_agent.models import JobPosting


@dataclass(frozen=True)
class VerificationResult:
    is_valid: bool
    reasons: list[str]


_ENGINEERING_ACTION = re.compile(
    r"开发|研发|实现|构建|搭建|接入|集成|编写|部署|优化|训练|评测|重构|维护|设计|排查|"
    r"\b(?:develop|build|implement|integrate|deploy|train|evaluate|optimi[sz]e|design|debug)\w*\b", re.I)
_ENGINEERING_OBJECT = re.compile(
    r"代码|模块|平台|系统|算法|工具调用|接口|服务|工作流|模型|框架|应用|智能体|工程|"
    r"\b(?:agent|agents|llm|rag|api|apis|software|model|models|workflow|workflows|backend|code|pipeline)\b", re.I)
_OTHER_DUTY = re.compile(
    r"数据标注|数据整理|内容创作|内容审核|用户运营|内容运营|社群运营|投放|市场推广|商务拓展|"
    r"运营数据|市场调研|竞品分析|用户调研|产品原型|需求文档|营销|客户服务|"
    r"\b(?:annotat\w+|marketing|community management|content moderation)\b", re.I)


def _primarily_development(duties: str) -> bool:
    engineering = other = 0
    clauses = re.split(r"[\n。；;!?！？]|(?<=\w)\.\s+", duties)
    for clause in {part.strip().casefold() for part in clauses if part.strip()}:
        technical = bool(_ENGINEERING_ACTION.search(clause) and _ENGINEERING_OBJECT.search(clause))
        nontechnical = bool(_OTHER_DUTY.search(clause))
        engineering += technical and not nontechnical
        other += nontechnical
    return engineering > other


class JobVerifier:
    def __init__(
        self,
        required_keywords: list[str] | None = None,
        min_confidence: float = 0.5,
        *,
        require_development: bool = False,
    ):
        self.required_keywords = [
            keyword.lower()
            for keyword in (required_keywords or list(AI_JOB_KEYWORDS))
            if keyword.strip()
        ]
        self.min_confidence = min_confidence
        self.require_development = require_development

    def verify(self, job: JobPosting) -> VerificationResult:
        reasons: list[str] = []
        if job.confidence < self.min_confidence:
            reasons.append(f"confidence below {self.min_confidence:g}")
        if not job.requirements.strip() and not job.responsibilities.strip():
            reasons.append("missing requirements and responsibilities")
        if not self._is_relevant(job):
            reasons.append("not relevant to AI job direction")
        if self.require_development and not _primarily_development(job.responsibilities):
            reasons.append("岗位职责未能确认以开发或技术研发为主")
        return VerificationResult(is_valid=not reasons, reasons=reasons)

    def dedupe(
        self,
        jobs: list[JobPosting],
    ) -> tuple[list[JobPosting], list[JobPosting]]:
        seen_urls: set[str] = set()
        seen_company_titles: set[tuple[str, str]] = set()
        unique: list[JobPosting] = []
        duplicates: list[JobPosting] = []

        for job in jobs:
            url_key = job.url.strip().lower()
            title_key = (
                job.company.strip().lower(),
                job.title.strip().lower(),
            )
            if url_key in seen_urls or title_key in seen_company_titles:
                duplicates.append(job)
                continue

            seen_urls.add(url_key)
            seen_company_titles.add(title_key)
            unique.append(job)

        return unique, duplicates

    def _is_relevant(self, job: JobPosting) -> bool:
        if not self.required_keywords:
            return True
        haystack = " ".join(
            [
                job.title,
                job.requirements,
                job.responsibilities,
                " ".join(job.skills),
            ]
        ).lower()
        return any(keyword in haystack for keyword in self.required_keywords)
