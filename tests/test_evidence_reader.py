import json

import pytest

from web_task_agent.llm_extractor import LlmProviderConfig
from web_task_agent.models import BrowserPage


def fixture():
    content = (
        "岗位：大模型算法实习生\n公司：示例科技有限公司\n工作地点：上海\n"
        "岗位职责：开发大模型推理和训练系统。\n任职要求：熟悉 Python，每周实习四天。\n"
    )
    job = {
        "title": {"value": "大模型算法实习生", "quote": "岗位：大模型算法实习生"},
        "company": {"value": "示例科技有限公司", "quote": "公司：示例科技有限公司"},
        "location": {"value": "上海", "quote": "工作地点：上海"},
        "responsibilities": ["开发大模型推理和训练系统。"],
        "requirements": ["熟悉 Python，每周实习四天。"],
        "is_ai_role": True, "role_evidence": ["开发大模型推理和训练系统。"],
        "is_internship": True, "internship_evidence": ["大模型算法实习生"],
    }
    return BrowserPage(url="https://example.org/jobs/1", content=content), {"page_type": "single_job", "jobs": [job]}


def test_supported_job_is_accepted_with_literal_source_fields():
    from web_task_agent.evidence_reader import PageReading, assess_reading

    page, output = fixture()
    result = assess_reading(PageReading.model_validate(output), page, keyword="大模型 实习", location="上海")
    assert result["status"] == "accepted"
    assert result["jobs"][0]["company"] == "示例科技有限公司"


@pytest.mark.parametrize("mutation", ["fabricated_quote", "salary_company", "missing_company", "role_from_intro"])
def test_unsupported_or_missing_facts_are_never_accepted(mutation):
    from web_task_agent.evidence_reader import PageReading, assess_reading

    page, output = fixture()
    job = output["jobs"][0]
    if mutation == "fabricated_quote":
        job["location"] = {"value": "北京", "quote": "工作地点：北京"}
    elif mutation == "salary_company":
        page.content += "\n薪资：15000-30000"
        job["company"] = {"value": "15000-30000", "quote": "薪资：15000-30000"}
    elif mutation == "missing_company":
        job["company"] = {"value": "", "quote": ""}
    else:
        page.content += "\n公司介绍：我们研发大模型产品。"
        job["role_evidence"] = ["我们研发大模型产品。"]
    result = assess_reading(PageReading.model_validate(output), page, keyword="大模型 实习", location="上海")
    assert result["status"] == "review"
    assert result["jobs"] == []


def test_hr_role_is_rejected_even_when_company_intro_mentions_ai():
    from web_task_agent.evidence_reader import PageReading, assess_reading

    page, output = fixture()
    page.content += "\n岗位职责：负责招聘和员工入离职。\n公司介绍：我们研发大模型产品。"
    job = output["jobs"][0]
    job.update(is_ai_role=False, role_evidence=["负责招聘和员工入离职。"],
               responsibilities=["负责招聘和员工入离职。"])
    result = assess_reading(PageReading.model_validate(output), page, keyword="大模型 实习", location="上海")
    assert result["status"] == "rejected"
    assert "not_ai_role" in result["reasons"]


def test_conflicting_location_is_reviewed_instead_of_picking_requested_city():
    from web_task_agent.evidence_reader import PageReading, assess_reading

    page, output = fixture()
    page.content += "\n工作地址：深圳市南山区。"
    output["jobs"][0].update(location_conflict=True, conflict_quotes=["工作地点：上海", "工作地址：深圳市南山区。"])
    result = assess_reading(PageReading.model_validate(output), page, keyword="大模型 实习", location="上海")
    assert result["status"] == "review"
    assert "location_conflict" in result["reasons"]


def test_list_page_is_not_counted_as_a_single_job():
    from web_task_agent.evidence_reader import PageReading, assess_reading

    page, output = fixture()
    output["page_type"] = "job_list"
    result = assess_reading(PageReading.model_validate(output), page, keyword="大模型 实习", location="上海")
    assert result["status"] == "review"
    assert result["jobs"] == []


def test_reader_uses_full_snapshot_including_tail_and_records_usage():
    from web_task_agent.evidence_reader import EvidenceJobReader

    page, output = fixture()
    page.content += "\n" + "正文" * 2000 + "\n尾部地址冲突，不得丢失"
    sent = []

    def transport(url, headers, payload, timeout):
        sent.append(payload)
        return {"choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}

    reader = EvidenceJobReader(LlmProviderConfig("deepseek", "test", "secret", "https://api.deepseek.com"),
                               transport=transport)
    result = reader.read(page)
    assert result["reading"]["page_type"] == "single_job"
    assert "尾部地址冲突，不得丢失" in sent[0]["messages"][-1]["content"]
    assert result["usage"]["total_tokens"] == 30
    assert "secret" not in json.dumps(result)


def test_malformed_model_output_is_an_error_not_a_rule_fallback():
    from web_task_agent.evidence_reader import EvidenceJobReader

    page, _ = fixture()
    reader = EvidenceJobReader(
        LlmProviderConfig("deepseek", "test", "secret", "https://api.deepseek.com"),
        transport=lambda *args: {"choices": [{"message": {"content": "not JSON secret"}}]},
    )
    result = reader.read(page)
    assert result["status"] == "error"
    assert result["error_type"] == "JSONDecodeError"
    assert "secret" not in json.dumps(result)


def test_deepseek_extraction_explicitly_disables_thinking_to_leave_budget_for_json():
    from web_task_agent.evidence_reader import EvidenceJobReader

    page, output = fixture()

    def transport(url, headers, payload, timeout):
        if payload.get("thinking") != {"type": "disabled"}:
            return {"choices": [{"finish_reason": "length", "message": {"content": ""}}],
                    "usage": {"completion_tokens": 5000, "completion_tokens_details": {"reasoning_tokens": 5000}}}
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}]}

    reader = EvidenceJobReader(LlmProviderConfig("deepseek", "test", "secret", "https://api.deepseek.com"),
                               transport=transport)
    assert reader.read(page)["status"] == "ok"


def test_truncated_response_is_recorded_without_publishing_reasoning():
    from web_task_agent.evidence_reader import EvidenceJobReader

    page, _ = fixture()
    reader = EvidenceJobReader(
        LlmProviderConfig("deepseek", "test", "secret", "https://api.deepseek.com"),
        transport=lambda *args: {"choices": [{"finish_reason": "length", "message": {
            "content": "", "reasoning_content": "private reasoning secret",
        }}], "usage": {"completion_tokens": 5000, "completion_tokens_details": {"reasoning_tokens": 5000}}},
    )
    result = reader.read(page)
    assert result["error_type"] == "OutputLimitError"
    assert result["finish_reason"] == "length"
    assert result["reasoning_tokens"] == 5000
    assert "secret" not in json.dumps(result)
