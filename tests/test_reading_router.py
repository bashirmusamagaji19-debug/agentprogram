"""Tests for the unified rules and evidence-backed reading router."""

from __future__ import annotations

import json

from web_task_agent.evidence_reader import EvidenceJobReader
from web_task_agent.llm_extractor import LlmProviderConfig
from web_task_agent.models import BrowserPage
from web_task_agent.planner_routing import ModelCallBudget
from web_task_agent.reading_router import (
    ReadingModelCache,
    ReadingResult,
    ReadingRouter,
    RulesPageReader,
    reading_input_sha256,
)

VALID_CONTENT = (
    "岗位：大模型算法实习生\n"
    "公司：示例科技有限公司\n"
    "工作地点：上海\n"
    "岗位职责：开发大模型推理和训练系统。\n"
    "任职要求：熟悉 Python，每周实习四天。\n"
)


def make_page(
    content: str = VALID_CONTENT,
    *,
    url: str = "https://example.org/jobs/1",
    title: str = "大模型算法实习生",
    metadata: dict | None = None,
) -> BrowserPage:
    return BrowserPage(url=url, title=title, content=content, metadata=metadata or {})


def model_payload(
    *,
    company_quote: str = "公司：示例科技有限公司",
    location_quote: str = "工作地点：上海",
    title_quote: str = "岗位：大模型算法实习生",
    responsibilities: tuple[str, ...] = ("岗位职责：开发大模型推理和训练系统。",),
    requirements: tuple[str, ...] = ("任职要求：熟悉 Python，每周实习四天。",),
    is_ai_role: bool = True,
    role_evidence: tuple[str, ...] = ("开发大模型推理和训练系统。",),
    is_internship: bool = True,
    internship_evidence: tuple[str, ...] = ("大模型算法实习生",),
    location_conflict: bool = False,
    conflict_quotes: tuple[str, ...] = (),
) -> dict:
    return {
        "status": "ok",
        "reading": {
            "page_type": "single_job",
            "jobs": [
                {
                    "title": {"value": "大模型算法实习生", "quote": title_quote},
                    "company": {"value": "示例科技有限公司", "quote": company_quote},
                    "location": {"value": "上海", "quote": location_quote},
                    "requirements": list(requirements),
                    "responsibilities": list(responsibilities),
                    "is_ai_role": is_ai_role,
                    "role_evidence": list(role_evidence),
                    "is_internship": is_internship,
                    "internship_evidence": list(internship_evidence),
                    "location_conflict": location_conflict,
                    "conflict_quotes": list(conflict_quotes),
                    "uncertainties": [],
                }
            ],
            "detail_links": [],
            "notes": [],
        },
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        "elapsed_seconds": 0.01,
    }


class FakeReader:
    def __init__(self, result: dict | Exception):
        self.result = result
        self.calls: list[BrowserPage] = []

    def read(self, page: BrowserPage) -> dict:
        self.calls.append(page)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_rules_reader_accepts_grounded_single_job_with_literal_quotes():
    page = make_page()
    result = ReadingRouter().read(page, "大模型 实习", "上海", "rules")

    assert isinstance(result, ReadingResult)
    assert result.status == "accepted"
    assert result.strategy == "rules"
    assert result.model_called is False
    assert result.model_used is False
    assert result.reading.page_type == "single_job"
    assert result.jobs[0]["company"] == "示例科技有限公司"
    assert result.input_sha256 == reading_input_sha256(page)
    assert result.reasons == []
    assert result["status"] == "accepted"
    assert result.get("missing_field") is None


def test_rules_page_reader_is_deterministic_and_uses_literal_source_spans():
    reading = RulesPageReader().read(make_page())

    assert reading.page_type == "single_job"
    assert reading.jobs[0].company.value == "示例科技有限公司"
    assert reading.jobs[0].company.quote == "公司：示例科技有限公司"


def test_always_calls_existing_evidence_reader_with_the_same_page_context():
    page = make_page()
    sent: list[dict] = []

    def transport(url, headers, payload, timeout):
        sent.append(payload)
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(model_payload()["reading"], ensure_ascii=False)},
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }

    reader = EvidenceJobReader(
        LlmProviderConfig("deepseek", "test", "secret", "https://api.deepseek.com"),
        transport=transport,
    )
    result = ReadingRouter(reader).read(page, "大模型 实习", "上海", "always")

    assert result.status == "accepted"
    assert result.model_called is True
    assert result.model_used is True
    assert result.usage["total_tokens"] == 30
    assert len(sent) == 1
    assert json.loads(sent[0]["messages"][-1]["content"])["content"] == page.content
    assert "secret" not in result.model_dump_json()


def test_adaptive_skips_model_when_rules_are_conclusive():
    reader = FakeReader(model_payload())
    page = make_page()
    result = ReadingRouter(reader).read(page, "大模型 实习", "上海", "adaptive")

    assert result.status == "accepted"
    assert result.route_reason == "rules_accepted"
    assert result.model_called is False
    assert reader.calls == []


def test_adaptive_escalates_missing_company_and_uses_model_gate():
    content = VALID_CONTENT.replace("公司：示例科技有限公司\n", "团队介绍：示例科技有限公司\n")
    page = make_page(content)
    reader = FakeReader(model_payload(company_quote="团队介绍：示例科技有限公司"))
    router = ReadingRouter(reader)

    rules = router.read(page, "大模型 实习", "上海", "rules")
    assert rules.status == "review"
    assert any("missing_company" in reason for reason in rules.reasons)

    result = router.read(page, "大模型 实习", "上海", "adaptive")
    assert result.status == "accepted"
    assert result.route_reason == "rules_review_escalated"
    assert result.model_called is True
    assert "missing_company" in result.escalation_reasons
    assert len(reader.calls) == 1


def test_cache_reuses_raw_model_reading_but_gates_each_condition_independently():
    reader = FakeReader(model_payload())
    router = ReadingRouter(reader)
    page = make_page()

    shanghai = router.read(page, "大模型 实习", "上海", "always")
    beijing = router.read(page, "大模型 实习", "北京", "always")

    assert shanghai.status == "accepted"
    assert beijing.status == "rejected"
    assert "location_mismatch" in beijing.reasons
    assert len(reader.calls) == 1
    assert shanghai.model_called is True
    assert shanghai.model_reused is False
    assert beijing.model_called is False
    assert beijing.model_reused is True
    assert beijing.input_sha256 == shanghai.input_sha256


def test_model_budget_blocks_before_the_reader_is_called():
    reader = FakeReader(model_payload())
    budget = ModelCallBudget(max_calls=0, max_observed_tokens=100_000, deadline_seconds=30)
    result = ReadingRouter(reader, budget=budget).read(
        make_page(), "大模型 实习", "上海", "always"
    )

    assert result.status == "error"
    assert result.error_type == "ModelBudgetExceeded"
    assert result.route_reason == "model_budget_exhausted"
    assert result.model_called is False
    assert reader.calls == []
    assert budget.calls == 0


def test_model_budget_counts_actual_calls_across_pages():
    reader = FakeReader(model_payload())
    budget = ModelCallBudget(max_calls=1, max_observed_tokens=100_000, deadline_seconds=30)
    router = ReadingRouter(reader, budget=budget)

    first = router.read(make_page(url="https://example.org/jobs/1"), "大模型 实习", "上海", "always")
    second = router.read(make_page(url="https://example.org/jobs/2"), "大模型 实习", "上海", "always")

    assert first.status == "accepted"
    assert second.status == "error"
    assert second.error_type == "ModelBudgetExceeded"
    assert len(reader.calls) == 1
    assert budget.calls == 1


def test_forged_model_quote_is_review_not_accepted():
    page = make_page()
    reader = FakeReader(
        model_payload(
            location_quote="工作地点：北京",
        )
    )
    result = ReadingRouter(reader).read(page, "大模型 实习", "上海", "always")

    assert result.status == "review"
    assert "unsupported_location" in result.reasons
    assert result.jobs == []
    assert result.model_called is True


def test_missing_body_is_reviewed_as_insufficient():
    result = ReadingRouter().read(make_page(""), "大模型 实习", "上海", "rules")

    assert result.status == "review"
    assert result.reading.page_type == "insufficient"
    assert result.jobs == []
    assert "insufficient" in result.reasons


def test_garbled_text_is_review_not_accepted():
    content = VALID_CONTENT.replace("熟悉 Python", "熟悉 Pyth�n")
    result = ReadingRouter().read(make_page(content), "大模型 实习", "上海", "rules")

    assert result.status == "review"
    assert "model_reported_uncertainty" in result.reasons
    assert result.jobs == []


def test_possible_truncation_is_review_not_accepted():
    content = VALID_CONTENT.replace("每周实习四天。", "每周实习四天…")
    result = ReadingRouter().read(make_page(content), "大模型 实习", "上海", "rules")

    assert result.status == "review"
    assert "model_reported_uncertainty" in result.reasons
    assert result.jobs == []


def test_work_city_label_is_a_literal_location_fallback():
    content = VALID_CONTENT.replace("工作地点：上海", "工作城市：上海")
    result = ReadingRouter().read(make_page(content), "大模型 实习", "上海", "rules")

    assert result.status == "accepted"
    assert result.reading.jobs[0].location.value == "上海"
    assert result.reading.jobs[0].location.quote == "工作城市：上海"


def test_company_intro_without_a_job_is_rejected_as_not_job():
    content = "公司介绍：我们研发大模型和 Agent 产品，服务企业客户。\n" * 3
    result = ReadingRouter().read(make_page(content), "大模型 实习", "上海", "rules")

    assert result.status == "rejected"
    assert "not_job" in result.reasons
    assert result.jobs == []


def test_salary_shaped_company_is_reviewed_not_accepted():
    content = VALID_CONTENT.replace("公司：示例科技有限公司", "公司：15000-30000")
    result = ReadingRouter().read(make_page(content), "大模型 实习", "上海", "rules")

    assert result.status == "review"
    assert "invalid_company" in result.reasons
    assert result.jobs == []


def test_ai_company_intro_does_not_make_hr_role_ai():
    content = (
        "岗位：HR实习生\n"
        "公司：示例AI科技有限公司\n"
        "工作地点：上海\n"
        "岗位职责：负责招聘、员工入职和离职手续。\n"
        "任职要求：熟悉办公软件，每周实习四天。\n"
        "公司介绍：我们研发大模型产品。\n"
    )
    page = make_page(content)
    reader = FakeReader(model_payload())
    router = ReadingRouter(reader)

    rules = router.read(page, "大模型 实习", "上海", "rules")
    adaptive = router.read(page, "大模型 实习", "上海", "adaptive")

    assert rules.status == "rejected"
    assert "not_ai_role" in rules.reasons
    assert adaptive.status == "rejected"
    assert adaptive.model_called is False
    assert reader.calls == []


def test_hr_title_does_not_become_ai_from_ai_team_duties():
    content = (
        "岗位：HR实习生\n"
        "公司：示例AI科技有限公司\n"
        "工作地点：上海\n"
        "岗位职责：负责大模型团队招聘和员工入职。\n"
        "任职要求：熟悉办公软件，每周实习四天。\n"
    )
    result = ReadingRouter().read(make_page(content), "大模型 实习", "上海", "rules")

    assert result.status == "rejected"
    assert "not_ai_role" in result.reasons
    assert result.jobs == []


def test_ai_recruitment_data_role_uses_technical_duties_not_a_title_substring():
    content = (
        "岗位：AI 招聘数据采集实习生\n"
        "公司：长风信息\n"
        "工作地点：西安\n"
        "岗位职责：维护公开岗位字段的一致性检查，编写 Python 采集脚本。\n"
        "任职要求：熟悉数据清洗与接口测试，每周实习四天。\n"
    )
    result = ReadingRouter().read(make_page(content), "AI 招聘数据采集 实习", "西安", "rules")

    assert result.status == "accepted"
    assert result.jobs[0]["title"] == "AI 招聘数据采集实习生"
    assert "not_ai_role" not in result.reasons


def test_same_city_footer_is_not_a_location_conflict():
    content = VALID_CONTENT + "工作地址：上海市浦东新区\n"
    result = ReadingRouter().read(make_page(content), "大模型 实习", "上海", "rules")

    assert result.status == "accepted"
    assert result.reading.jobs[0].location_conflict is False
    assert result.reading.jobs[0].conflict_quotes == []


def test_two_different_work_locations_are_reviewed():
    content = VALID_CONTENT + "工作地址：深圳市南山区\n"
    result = ReadingRouter().read(make_page(content), "大模型 实习", "上海", "rules")

    assert result.status == "review"
    assert "location_conflict" in result.reasons
    assert result.reading.jobs[0].location_conflict is True
    assert len(result.reading.jobs[0].conflict_quotes) == 2


def test_multi_job_notice_is_review_not_one_job():
    content = (
        "招聘公告\n"
        "岗位：大模型算法实习生\n"
        "公司：示例科技有限公司\n"
        "工作地点：上海\n"
        "岗位职责：开发大模型推理系统。\n"
        "任职要求：熟悉 Python，每周实习四天。\n"
        "岗位：数据分析实习生\n"
        "公司：示例科技有限公司\n"
        "工作地点：北京\n"
        "岗位职责：分析招聘数据。\n"
        "任职要求：熟悉 SQL，每周实习四天。\n"
    )
    result = ReadingRouter().read(make_page(content), "大模型 实习", "上海", "rules")

    assert result.status == "review"
    assert result.reading.page_type in {"recruitment_notice", "job_list"}
    assert result.jobs == []
    assert result.reading.page_type in result.reasons


def test_adaptive_calls_model_for_multi_job_but_forces_review():
    content = (
        "招聘公告\n"
        "岗位：大模型算法实习生\n"
        "公司：示例科技有限公司\n"
        "工作地点：上海\n"
        "岗位职责：开发大模型推理系统。\n"
        "任职要求：熟悉 Python，每周实习四天。\n"
        "岗位：数据分析实习生\n"
        "公司：示例科技有限公司\n"
        "工作地点：北京\n"
        "岗位职责：分析招聘数据。\n"
        "任职要求：熟悉 SQL，每周实习四天。\n"
    )
    reader = FakeReader(model_payload())
    result = ReadingRouter(reader).read(make_page(content), "大模型 实习", "上海", "adaptive")

    assert result.status == "review"
    assert result.route_reason == "multi_job_review"
    assert result.model_called is True
    assert len(reader.calls) == 1


def test_always_cannot_publish_one_job_from_a_multi_job_notice():
    content = (
        "招聘公告\n"
        "岗位：大模型算法实习生\n"
        "公司：示例科技有限公司\n"
        "工作地点：上海\n"
        "岗位职责：开发大模型推理系统。\n"
        "任职要求：熟悉 Python，每周实习四天。\n"
        "岗位：数据分析实习生\n"
        "公司：示例科技有限公司\n"
        "工作地点：北京\n"
        "岗位职责：分析招聘数据。\n"
        "任职要求：熟悉 SQL，每周实习四天。\n"
    )
    reader = FakeReader(model_payload())
    result = ReadingRouter(reader).read(make_page(content), "大模型 实习", "上海", "always")

    assert result.status == "review"
    assert result.route_reason == "multi_job_review"
    assert result.model_called is True
    assert len(reader.calls) == 1
    assert result.jobs == []
    assert result.reading.page_type in {"recruitment_notice", "job_list"}


def test_model_error_stays_error_and_does_not_fall_back_to_rules():
    content = VALID_CONTENT.replace("公司：示例科技有限公司\n", "团队介绍：示例科技有限公司\n")
    reader = FakeReader({"status": "error", "error_type": "TimeoutError", "usage": {}})
    result = ReadingRouter(reader).read(make_page(content), "大模型 实习", "上海", "adaptive")

    assert result.status == "error"
    assert result.error_type == "TimeoutError"
    assert result.model_called is True
    assert result.reading is None
    assert result.jobs == []


def test_rules_reader_ignores_candidate_metadata_that_the_model_reader_cannot_see():
    content = VALID_CONTENT.replace("公司：示例科技有限公司\n", "")
    page = make_page(content, metadata={"discovered_company": "示例科技有限公司"})
    result = ReadingRouter().read(page, "大模型 实习", "上海", "rules")

    assert result.status == "review"
    assert any("missing_company" in reason for reason in result.reasons)
    assert result.jobs == []


def test_reading_model_cache_can_be_reused_explicitly():
    cache = ReadingModelCache()
    reader = FakeReader(model_payload())
    router = ReadingRouter(reader, cache=cache)
    page = make_page()

    first = router.read(page, "大模型 实习", "上海", "always")
    second = router.read(page, "大模型 实习", "上海", "always")

    assert len(cache) == 1
    assert first.model_called is True
    assert second.model_reused is True
    assert len(reader.calls) == 1
