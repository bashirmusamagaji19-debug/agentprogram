"""Unified deterministic and evidence-backed page reading for C4 experiments.

The rules path parses literal spans and the model path delegates to the
existing EvidenceJobReader. Both paths pass through the same assess_reading
gate. No network fetching happens here.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from time import perf_counter
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from web_task_agent.evidence_reader import GroundedJob, PageReading, QuotedField, assess_reading
from web_task_agent.extractor import PageExtractor
from web_task_agent.keywords import AI_JOB_KEYWORDS
from web_task_agent.location_filter import is_unknown_location, location_matches
from web_task_agent.models import BrowserPage
from web_task_agent.planner_routing import ModelBudgetExceeded, ModelCallBudget

__all__ = [
    "ReadingModelCache",
    "ReadingResult",
    "ReadingRouter",
    "RulesPageReader",
    "reading_input_sha256",
    "route_reading",
]

ReadingStrategy = Literal["rules", "always", "adaptive"]
ReadingStatus = Literal["accepted", "rejected", "review", "error"]

STRATEGIES: tuple[ReadingStrategy, ...] = ("rules", "always", "adaptive")
_MIN_READABLE_CHARS = 40
_UNKNOWN_VALUES = {
    "unknown",
    "unknown title",
    "unknown company",
    "unknown location",
    "n/a",
    "none",
}
_STOP_SECTION_MARKERS = (
    "about us",
    "benefits",
    "company intro",
    "company introduction",
    "contact",
    "deadline",
    "footer",
    "公司介绍",
    "公司简介",
    "关于我们",
    "其他信息",
    "联系方式",
    "投递方式",
    "截止日期",
    "福利",
    "薪资",
    "页脚",
)
_RECRUITMENT_MARKERS = (
    "recruitment notice",
    "job list",
    "岗位一览",
    "岗位列表",
    "招聘公告",
    "招聘简章",
    "招聘岗位",
    "招聘职位",
    "校招公告",
    "职位列表",
)
_JOB_MARKERS = ("career", "intern", "job", "岗位", "职位", "实习", "招聘")
_NON_TECHNICAL_TITLE_MARKERS = (
    "human resources",
    "hrbp",
    "people operations",
    "administrative",
    "administration",
    "finance",
    "legal",
    "marketing",
    "sales",
    "人事",
    "人力资源",
    "行政",
    "法务",
    "财务",
    "市场",
    "销售",
)
_RECRUITMENT_TITLE_MARKERS = ("recruit", "talent", "招聘")
_TECHNICAL_DUTY_MARKERS = (
    "api",
    "python",
    "sql",
    "工程",
    "接口",
    "数据",
    "模型",
    "算法",
    "测试",
    "采集",
)
_HR_WORD = re.compile(r"(?:^|[^a-z])hr(?:[^a-z]|$)")
_URL_PATTERN = re.compile(r"https?://[^\s<>\"'）)\]]+")
_ACTIVE_AI_KEYWORDS = tuple(keyword.casefold() for keyword in AI_JOB_KEYWORDS)
_LABEL_MAP = {
    label.casefold(): field
    for field, labels in PageExtractor._LABELS.items()
    for label in labels
}
_LABEL_MAP.update(
    {
        "address": "location",
        "job location": "location",
        "work city": "location",
        "work address": "location",
        "work location": "location",
        "工作城市": "location",
        "工作地址": "location",
    }
)


class ModelPageReader(Protocol):
    def read(self, page: BrowserPage) -> dict[str, Any]:
        ...


class ReadingResult(BaseModel):
    """The same observable result shape for every reading strategy."""

    model_config = ConfigDict(extra="forbid")

    status: ReadingStatus
    reasons: list[str] = Field(default_factory=list)
    jobs: list[dict[str, Any]] = Field(default_factory=list)
    strategy: ReadingStrategy
    keyword: str
    location: str
    input_sha256: str
    reading: PageReading | None = None
    model_called: bool = False
    model_reused: bool = False
    model_used: bool = False
    route_reason: str = ""
    escalation_reasons: list[str] = Field(default_factory=list)
    error_type: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    elapsed_seconds: float | None = Field(default=None, ge=0)

    def __getitem__(self, key: str) -> Any:
        if key not in type(self).model_fields:
            raise KeyError(key)
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in type(self).model_fields


class ReadingModelCache:
    """In-memory raw reading cache keyed by the page input hash."""

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._entries.get(key)

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._entries[key] = value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def reading_input_sha256(page: BrowserPage) -> str:
    """Hash exactly the model-visible page input, excluding conditions."""

    payload = json.dumps(
        [page.url, page.title, page.content],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_page(page: BrowserPage) -> BrowserPage:
    # Candidate metadata is not part of EvidenceJobReader's input. Keeping it
    # out of the rules path makes both readers use the same url/title/content.
    return BrowserPage(
        url=page.url,
        title=page.title,
        content=page.content,
        source=page.source,
        metadata={},
    )


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _split_label(line: str) -> tuple[str, str, str]:
    label, separator, value = line.partition(":")
    if not separator:
        label, separator, value = line.partition("：")
    return label, separator, value


def _label_field(line: str) -> str | None:
    label, separator, _ = _split_label(line)
    key = (label if separator else line).strip().casefold()
    return _LABEL_MAP.get(key)


def _normalized(text: str) -> str:
    return re.sub(r"[\s*_\x60#\\]", "", unicodedata.normalize("NFKC", text)).casefold()


def _known_value(value: str) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() in _UNKNOWN_VALUES else text


def _quote_for_value(value: str, page: BrowserPage) -> str:
    value = value.strip()
    if not value:
        return ""
    normalized_value = _normalized(value)
    for line in page.content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if value in stripped or normalized_value in _normalized(stripped):
            return stripped
    if value in page.content:
        return value
    if value in page.title or normalized_value in _normalized(page.title):
        return page.title.strip()
    return ""


def _dedupe(values: list[str], limit: int = 20) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = value.strip()
        key = _normalized(item)
        if item and key not in seen:
            seen.add(key)
            result.append(item)
        if len(result) >= limit:
            break
    return result


def _is_stop_line(line: str) -> bool:
    value = line.strip().casefold()
    return any(value.startswith(marker) for marker in _STOP_SECTION_MARKERS)


def _section_quotes(lines: list[str], field: str) -> list[str]:
    quotes: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        mapped = _label_field(line)
        if mapped != field:
            index += 1
            continue
        _, _, value = _split_label(line)
        value = value.strip()
        if value:
            quotes.append(line)
            index += 1
            continue
        cursor = index + 1
        while cursor < len(lines):
            if _label_field(lines[cursor]) is not None or _is_stop_line(lines[cursor]):
                break
            quotes.append(lines[cursor])
            cursor += 1
        index = max(cursor, index + 1)
    return _dedupe(quotes)


def _literal_fragments(value: str, page: BrowserPage) -> list[str]:
    if not value.strip():
        return []
    fragments: list[str] = []
    normalized_value = _normalized(value)
    for line in _lines(page.content):
        if line in value or _normalized(line) in normalized_value:
            fragments.append(line)
    if fragments:
        return _dedupe(fragments)
    for fragment in re.split(r"(?<=[。！？.!?;；])\s*|\n+", value):
        fragment = fragment.strip()
        if fragment and fragment in page.content:
            fragments.append(fragment)
    return _dedupe(fragments)


def _literal_urls(content: str) -> list[str]:
    links: list[str] = []
    for match in _URL_PATTERN.findall(content):
        url = match.rstrip(".,;，。；)")
        if url not in links:
            links.append(url)
    return links[:30]


def _has_recruitment_marker(content: str) -> bool:
    lowered = content.casefold()
    return any(marker in lowered for marker in _RECRUITMENT_MARKERS)


def _contains_ai(text: str) -> bool:
    lowered = text.casefold()
    for keyword in _ACTIVE_AI_KEYWORDS:
        if any("\u3400" <= character <= "\u9fff" for character in keyword):
            if keyword in lowered:
                return True
        elif re.search(rf"(?<![a-z0-9]){re.escape(keyword)}", lowered):
            return True
    return False


def _contains_intern(text: str) -> bool:
    if "实习" in text:
        return True
    return re.search(r"\bintern(?:ship|s)?\b", text.casefold()) is not None


def _is_non_technical_title(title: str, responsibilities: Sequence[str] = ()) -> bool:
    lowered = title.casefold()
    if _HR_WORD.search(lowered) is not None:
        return True
    if any(marker in lowered for marker in _NON_TECHNICAL_TITLE_MARKERS):
        return True
    if any(marker in lowered for marker in _RECRUITMENT_TITLE_MARKERS):
        duty_text = "\n".join(responsibilities).casefold()
        technical_data_role = _contains_ai(title) and any(
            marker in lowered or marker in duty_text
            for marker in _TECHNICAL_DUTY_MARKERS
        )
        return not technical_data_role
    return False


def _title_values(lines: list[str]) -> list[str]:
    values: list[str] = []
    for line in lines:
        if _label_field(line) != "title":
            continue
        _, _, value = _split_label(line)
        if value.strip():
            values.append(value.strip())
    return _dedupe(values, limit=10)


def _page_type(page: BrowserPage, lines: list[str], links: list[str]) -> str:
    counts = Counter(field for line in lines if (field := _label_field(line)))
    responsibility_count = counts["responsibilities"]
    requirements_count = counts["requirements"]
    marker = _has_recruitment_marker(page.content)
    multi_job = len(_title_values(lines)) >= 2 or (
        responsibility_count >= 2 and requirements_count >= 2
    )
    if multi_job:
        return "recruitment_notice" if marker else "job_list"
    if marker and len(links) >= 2 and len(lines) >= 4:
        return "recruitment_notice"
    if len(page.content.strip()) < _MIN_READABLE_CHARS:
        return "insufficient"
    structural = any(
        counts[field]
        for field in ("title", "company", "location", "requirements", "responsibilities")
    )
    if not structural:
        lowered = page.content.casefold()
        return "insufficient" if any(marker in lowered for marker in _JOB_MARKERS) else "not_job"
    return "single_job"


def _multi_job_type(page: BrowserPage) -> str | None:
    page_type = _page_type(page, _lines(page.content), _literal_urls(page.content))
    return page_type if page_type in {"job_list", "recruitment_notice"} else None


def _same_location(left: str, right: str) -> bool:
    if is_unknown_location(left) or is_unknown_location(right):
        return True
    return location_matches(left, right) or location_matches(right, left)


def _labeled_locations(page: BrowserPage) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line in _lines(page.content):
        if _label_field(line) != "location":
            continue
        _, _, value = _split_label(line)
        if value.strip():
            entries.append((value.strip(), line))
    return entries


def _detect_location_conflict(
    page: BrowserPage,
    extracted_location: str,
) -> tuple[bool, list[str]]:
    entries = _labeled_locations(page)
    if extracted_location and not is_unknown_location(extracted_location):
        quote = _quote_for_value(extracted_location, page)
        if quote:
            entries.append((extracted_location, quote))

    seen_quotes: set[str] = set()
    unique_entries: list[tuple[str, str]] = []
    for value, quote in entries:
        key = _normalized(quote)
        if key and key not in seen_quotes:
            seen_quotes.add(key)
            unique_entries.append((value, quote))
    for left_index, (left_value, left_quote) in enumerate(unique_entries):
        for right_value, right_quote in unique_entries[left_index + 1 :]:
            if not _same_location(left_value, right_value):
                return True, [left_quote, right_quote]
    return False, []


def _uncertainties(page: BrowserPage) -> list[str]:
    text = page.title + "\n" + page.content
    if any(marker in text for marker in ("\ufffd", "�", "□")):
        return ["garbled_text"]
    if re.search(r"\.{3,}|…", text):
        return ["possibly_truncated_text"]
    return []


class RulesPageReader:
    """Deterministic page reading with literal source quotes only."""

    def __init__(self, extractor: PageExtractor | None = None) -> None:
        self.extractor = extractor if extractor is not None else PageExtractor()

    def read(self, page: BrowserPage) -> PageReading:
        source = _source_page(page)
        lines = _lines(source.content)
        links = _literal_urls(source.content)
        page_type = _page_type(source, lines, links)
        if page_type != "single_job":
            return PageReading(
                page_type=page_type,
                jobs=[],
                detail_links=links,
                notes=[],
            )

        job = self.extractor.extract(source)
        title_value = _known_value(job.title) or source.title.strip()
        company_value = _known_value(job.company)
        extracted_location = _known_value(job.location)
        labeled_locations = _labeled_locations(source)
        if labeled_locations:
            location_value, location_quote = labeled_locations[0]
        else:
            location_value = extracted_location
            location_quote = _quote_for_value(location_value, source)
        title_quote = _quote_for_value(title_value, source)
        company_quote = _quote_for_value(company_value, source)
        requirements = _section_quotes(lines, "requirements") or _literal_fragments(
            job.requirements, source
        )
        responsibilities = _section_quotes(lines, "responsibilities") or _literal_fragments(
            job.responsibilities, source
        )

        ai_responsibilities = [quote for quote in responsibilities if _contains_ai(quote)]
        if ai_responsibilities:
            role_evidence = ai_responsibilities[:3]
        elif title_quote and _contains_ai(title_quote):
            role_evidence = [title_quote]
        elif responsibilities:
            role_evidence = responsibilities[:1]
        elif title_quote:
            role_evidence = [title_quote]
        else:
            role_evidence = []

        internship_candidates = [title_quote, *requirements, *responsibilities]
        internship_evidence = [
            quote for quote in internship_candidates if quote and _contains_intern(quote)
        ][:3]
        location_conflict, conflict_quotes = _detect_location_conflict(source, extracted_location)
        grounded = GroundedJob(
            title=QuotedField(value=title_value, quote=title_quote),
            company=QuotedField(value=company_value, quote=company_quote),
            location=QuotedField(value=location_value, quote=location_quote),
            requirements=requirements[:20],
            responsibilities=responsibilities[:20],
            is_ai_role=(
                _contains_ai(title_value + "\n" + "\n".join(responsibilities))
                and not _is_non_technical_title(title_value, responsibilities)
            ),
            role_evidence=role_evidence[:10],
            is_internship=bool(internship_evidence),
            internship_evidence=internship_evidence[:10],
            location_conflict=location_conflict,
            conflict_quotes=conflict_quotes[:10],
            uncertainties=_uncertainties(source)[:10],
        )
        return PageReading(
            page_type="single_job",
            jobs=[grounded],
            detail_links=links,
            notes=[],
        )


def _usage(raw: Mapping[str, Any]) -> dict[str, int]:
    value = raw.get("usage")
    if not isinstance(value, Mapping):
        return {}
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str)
        and isinstance(item, int)
        and not isinstance(item, bool)
        and item >= 0
    }


def _estimated_tokens(page: BrowserPage) -> int:
    return len(page.content.encode("utf-8")) + len(page.title.encode("utf-8")) + 5000


class ReadingRouter:
    """Run rules, always-model, or adaptive-model reading through one gate."""

    def __init__(
        self,
        model_reader: ModelPageReader | None = None,
        *,
        rules_reader: RulesPageReader | None = None,
        cache: ReadingModelCache | None = None,
        budget: ModelCallBudget | None = None,
    ) -> None:
        self.model_reader = model_reader
        self.rules_reader = rules_reader if rules_reader is not None else RulesPageReader()
        self.cache = cache if cache is not None else ReadingModelCache()
        self.budget = budget

    def read(
        self,
        page: BrowserPage,
        keyword: str,
        location: str,
        strategy: str = "rules",
    ) -> ReadingResult:
        if strategy not in STRATEGIES:
            raise ValueError("strategy must be rules, always, or adaptive")
        started = perf_counter()
        source = _source_page(page)
        if strategy == "rules":
            return self._rules_result(source, keyword, location, "rules", "rules_strategy", started)
        if strategy == "always":
            result = self._model_result(
                source,
                keyword,
                location,
                "always",
                route_reason="always_model",
                escalation_reasons=[],
                started=started,
            )
            multi_job = _multi_job_type(source)
            if multi_job is not None and result.status != "error":
                return self._multi_job_result(
                    result,
                    source,
                    multi_job,
                    keyword,
                    location,
                    started,
                )
            return result

        rules = self._rules_result(source, keyword, location, "adaptive", "rules_strategy", started)
        if rules.status == "error":
            return rules
        if rules.status in {"accepted", "rejected"} and not self._should_escalate(rules):
            return rules.model_copy(update={"route_reason": f"rules_{rules.status}"})
        if not self._should_escalate(rules):
            return rules.model_copy(update={"route_reason": "rules_review_conclusive"})
        result = self._model_result(
            source,
            keyword,
            location,
            "adaptive",
            route_reason="rules_review_escalated",
            escalation_reasons=rules.reasons,
            started=started,
        )
        multi_job = _multi_job_type(source)
        if multi_job is not None and result.status != "error":
            return self._multi_job_result(
                result,
                source,
                multi_job,
                keyword,
                location,
                started,
            )
        return result

    @staticmethod
    def _should_escalate(result: ReadingResult) -> bool:
        if result.status == "review":
            return True
        return result.status == "rejected" and "internship_not_established" in result.reasons

    def _rules_result(
        self,
        page: BrowserPage,
        keyword: str,
        location: str,
        strategy: ReadingStrategy,
        route_reason: str,
        started: float,
    ) -> ReadingResult:
        try:
            reading = self.rules_reader.read(page)
            gate = assess_reading(reading, page, keyword=keyword, location=location)
        except Exception as error:
            return self._error_result(
                page,
                keyword,
                location,
                strategy,
                error_type=type(error).__name__,
                route_reason="rules_error",
                started=started,
            )
        return ReadingResult(
            status=gate["status"],
            reasons=gate["reasons"],
            jobs=gate["jobs"],
            strategy=strategy,
            keyword=keyword,
            location=location,
            input_sha256=reading_input_sha256(page),
            reading=reading,
            route_reason=route_reason,
            elapsed_seconds=perf_counter() - started,
        )

    def _model_result(
        self,
        page: BrowserPage,
        keyword: str,
        location: str,
        strategy: ReadingStrategy,
        *,
        route_reason: str,
        escalation_reasons: list[str],
        started: float,
    ) -> ReadingResult:
        key = reading_input_sha256(page)
        cached = self.cache.get(key)
        reused = cached is not None
        raw = cached
        called = False

        if raw is None:
            if self.model_reader is None:
                result = self._error_result(
                    page,
                    keyword,
                    location,
                    strategy,
                    error_type="ModelReaderUnavailable",
                    route_reason="model_unavailable",
                    started=started,
                )
                return result.model_copy(update={"escalation_reasons": escalation_reasons})

            reservation: str | None = None
            if self.budget is not None:
                try:
                    reservation = self.budget.reserve(estimated_tokens=_estimated_tokens(page))
                except ModelBudgetExceeded:
                    result = self._error_result(
                        page,
                        keyword,
                        location,
                        strategy,
                        error_type="ModelBudgetExceeded",
                        route_reason="model_budget_exhausted",
                        started=started,
                    )
                    return result.model_copy(update={"escalation_reasons": escalation_reasons})

            called = True
            try:
                raw = self.model_reader.read(page)
            except Exception as error:
                raw = {"status": "error", "error_type": type(error).__name__, "usage": {}}
            finally:
                if reservation is not None and self.budget is not None:
                    usage = raw.get("usage") if isinstance(raw, dict) else None
                    self.budget.finish(reservation, usage=usage)
            if not isinstance(raw, dict):
                raw = {"status": "error", "error_type": "InvalidModelResult", "usage": {}}
            self.cache.put(key, raw)

        usage = _usage(raw)
        error_type = str(raw.get("error_type") or "ModelReadingError")
        if raw.get("status") != "ok":
            return ReadingResult(
                status="error",
                reasons=[error_type],
                jobs=[],
                strategy=strategy,
                keyword=keyword,
                location=location,
                input_sha256=key,
                model_called=called,
                model_reused=reused,
                model_used=True,
                route_reason="model_cache_reused" if reused else "model_error",
                escalation_reasons=escalation_reasons,
                error_type=error_type,
                usage=usage,
                elapsed_seconds=perf_counter() - started,
            )

        try:
            reading = PageReading.model_validate(raw.get("reading"))
            gate = assess_reading(reading, page, keyword=keyword, location=location)
        except Exception as error:
            return ReadingResult(
                status="error",
                reasons=[type(error).__name__],
                jobs=[],
                strategy=strategy,
                keyword=keyword,
                location=location,
                input_sha256=key,
                model_called=called,
                model_reused=reused,
                model_used=True,
                route_reason="model_cache_reused" if reused else "model_error",
                escalation_reasons=escalation_reasons,
                error_type=type(error).__name__,
                usage=usage,
                elapsed_seconds=perf_counter() - started,
            )

        return ReadingResult(
            status=gate["status"],
            reasons=gate["reasons"],
            jobs=gate["jobs"],
            strategy=strategy,
            keyword=keyword,
            location=location,
            input_sha256=key,
            reading=reading,
            model_called=called,
            model_reused=reused,
            model_used=True,
            route_reason="model_cache_reused" if reused else route_reason,
            escalation_reasons=escalation_reasons,
            usage=usage,
            elapsed_seconds=perf_counter() - started,
        )

    def _error_result(
        self,
        page: BrowserPage,
        keyword: str,
        location: str,
        strategy: ReadingStrategy,
        *,
        error_type: str,
        route_reason: str,
        started: float,
    ) -> ReadingResult:
        return ReadingResult(
            status="error",
            reasons=[error_type],
            jobs=[],
            strategy=strategy,
            keyword=keyword,
            location=location,
            input_sha256=reading_input_sha256(page),
            route_reason=route_reason,
            error_type=error_type,
            elapsed_seconds=perf_counter() - started,
        )

    def _multi_job_result(
        self,
        result: ReadingResult,
        page: BrowserPage,
        page_type: str,
        keyword: str,
        location: str,
        started: float,
    ) -> ReadingResult:
        guard = PageReading(
            page_type=page_type,
            jobs=[],
            detail_links=_literal_urls(page.content),
            notes=[],
        )
        return ReadingResult(
            status="review",
            reasons=[page_type],
            jobs=[],
            strategy=result.strategy,
            keyword=keyword,
            location=location,
            input_sha256=result.input_sha256,
            reading=guard,
            model_called=result.model_called,
            model_reused=result.model_reused,
            model_used=result.model_used,
            route_reason="multi_job_review",
            escalation_reasons=result.escalation_reasons,
            usage=result.usage,
            elapsed_seconds=perf_counter() - started,
        )


def route_reading(
    page: BrowserPage,
    keyword: str,
    location: str,
    strategy: str = "rules",
    *,
    model_reader: ModelPageReader | None = None,
    rules_reader: RulesPageReader | None = None,
    cache: ReadingModelCache | None = None,
    budget: ModelCallBudget | None = None,
) -> ReadingResult:
    """Convenience one-shot router; use ReadingRouter when sharing a cache."""

    return ReadingRouter(
        model_reader,
        rules_reader=rules_reader,
        cache=cache,
        budget=budget,
    ).read(page, keyword, location, strategy)
