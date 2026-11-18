"""Explicit AND keyword and OR location conditions, shared by discovery and validation."""

from __future__ import annotations

import re
import unicodedata

from web_task_agent.baidu_recruitment import BAIDU_DETAIL_TYPE_SOURCE, BAIDU_LIST_TYPE_SOURCE
from web_task_agent.browser import MIN_USEFUL_CONTENT_CHARS
from web_task_agent.location_filter import is_unknown_location, location_matches
from web_task_agent.skill_aliases import term_in_text

_SEPARATORS = re.compile(r"[\s,，、;；|]+")
_ALIAS_GROUPS = (
    ("ai", "人工智能", "artificial intelligence"),
    ("agent", "agents", "智能体"),
    ("intern", "interns", "internship", "internships", "实习", "实习生"),
)
_ALIASES = {alias: group for group in _ALIAS_GROUPS for alias in group}
_BAIDU_INTERN_TYPE_SOURCES = {BAIDU_LIST_TYPE_SOURCE, BAIDU_DETAIL_TYPE_SOURCE}
_HUAWEI_INTERN_TYPE_SOURCES = {"huawei:list:scenarioName", "huawei:detail:scenarioName"}
_OFFICIAL_TYPE_SOURCES = {
    "xiaomi:list:type", "xiaomi:detail:recruit_type.id", "ctrip:list:kind", "jd:list:planName",
    "liauto:list:job_mode", "feishu:detail:recruit_type.name", "tencent:detail:recruitType",
    "tencent:list:project",
} | _BAIDU_INTERN_TYPE_SOURCES | _HUAWEI_INTERN_TYPE_SOURCES


def parse_keywords(keyword: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", keyword).casefold().strip()
    return list(dict.fromkeys(term for term in _SEPARATORS.split(normalized) if term))


def missing_keywords(keyword: str, text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return [
        term for term in parse_keywords(keyword)
        if not any(term_in_text(alias, normalized) for alias in _ALIASES.get(term, (term,)))
    ]


def has_official_internship(source: str, value: str) -> bool:
    if source in _BAIDU_INTERN_TYPE_SOURCES:
        return value == "INTERN"
    if source in _HUAWEI_INTERN_TYPE_SOURCES:
        return value == "实习生"
    if source == "jd:list:planName":
        return value.strip().endswith(("实习生", "实习生计划"))
    # Values verified against public list/detail responses on 2026-10-25.
    return (source, value) in {
        ("ctrip:list:kind", "3"), ("tencent:list:project", "internship"), ("tencent:detail:recruitType", "2"),
        ("xiaomi:list:type", "3"), ("xiaomi:detail:recruit_type.id", "202"),
        ("liauto:list:job_mode", "202"),
    } or (source == "feishu:detail:recruit_type.name"
          and value.strip().casefold() in {"实习", "实习生", "intern", "internship"})


def _internship_requested(keyword: str) -> bool:
    return any(term in _ALIASES["intern"] for term in parse_keywords(keyword))


def _explicit_internship_text(title: str, body: str) -> bool:
    if not missing_keywords("intern", title):
        return True
    # Experience or an optional preference does not establish the position's
    # employment type. Require explicit role wording or a duration requirement.
    for line in re.split(r"[\n。；;]", body):
        if re.search(r"(?:经验|经历|者优先|experience|preferred)", line, re.I):
            continue
        if re.search(r"(?:岗位|职位|工作性质|招聘类型)\s*[:：]?\s*(?:为)?实习|"
                     r"(?:可|需|能|至少|保证)\s*实习\s*\d+\s*个?月|"
                     r"实习(?:时间|时长|周期|期)\s*[:：]?|"
                     r"\binternship\s+(?:position|role|duration)\b", line, re.I):
            return True
    return False


def condition_text(text: str, internship_evidence_source: str = "", internship_evidence_value: str = "") -> str:
    if has_official_internship(internship_evidence_source, internship_evidence_value):
        return text + "\n实习"
    return text


def candidate_matches(keyword: str, requested_location: str, *, title: str, jd_text: str, location: str,
                      internship_evidence_source: str = "", internship_evidence_value: str = "") -> bool:
    # A list may omit city or JD. Only reject known mismatches here; details must
    # establish every condition before returning a result.
    if not is_unknown_location(location) and not location_matches(requested_location, location):
        return False
    if (_internship_requested(keyword) and internship_evidence_source in _OFFICIAL_TYPE_SOURCES
            and internship_evidence_value
            and not has_official_internship(internship_evidence_source, internship_evidence_value)):
        return False
    return len(jd_text.strip()) < MIN_USEFUL_CONTENT_CHARS or not missing_keywords(
        keyword, condition_text(f"{title}\n{jd_text}", internship_evidence_source, internship_evidence_value))


def job_condition_reasons(keyword: str, requested_location: str, job) -> list[str]:
    reasons = []
    text = "\n".join([job.title, job.requirements, job.responsibilities, " ".join(job.skills)])
    missing = missing_keywords(keyword, condition_text(
        text, job.internship_evidence_source, job.internship_evidence_value))
    if missing:
        reasons.append("keyword mismatch: missing " + ", ".join(missing))
    if _internship_requested(keyword):
        source, value = job.internship_evidence_source, job.internship_evidence_value
        supported_type = source in _OFFICIAL_TYPE_SOURCES
        if supported_type and value and not has_official_internship(source, value):
            reasons.append("recruitment type mismatch: official type is not internship")
        elif not has_official_internship(source, value) and not _explicit_internship_text(
                job.title, job.requirements + "\n" + job.responsibilities):
            reasons.append("recruitment type unconfirmed: internship experience is not a job type")
    if not location_matches(requested_location, job.location):
        reasons.append(
            f"location unknown: requested {requested_location}"
            if is_unknown_location(job.location)
            else f"location mismatch: requested {requested_location}, got {job.location}"
        )
    return reasons
