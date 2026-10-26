import pytest

from web_task_agent.models import JobPosting
from web_task_agent.search_conditions import job_condition_reasons


def job(**overrides):
    fields = dict(title="AI Agent工程师", company="Example", location="北京", source="official",
                  url="https://example.com/jobs/123", responsibilities="开发 AI Agent 应用",
                  requirements="熟悉 Python，有实习经历优先")
    fields.update(overrides)
    return JobPosting(**fields)


@pytest.mark.parametrize("text", [
    "有实习经验者优先", "有大模型项目实践或实习经历优先", "能保证6个月以上长期实习者优先"])
def test_experience_or_preference_does_not_establish_an_internship(text):
    assert job_condition_reasons("AI Agent 实习", "全国", job(requirements=text))


@pytest.mark.parametrize("source,value", [("liauto:list:job_mode", "101"),
                                         ("feishu:detail:recruit_type.name", "全职")])
def test_native_fulltime_overrides_intern_mentions(source, value):
    assert job_condition_reasons("AI Agent 实习", "全国", job(title="AI Agent实习生",
        internship_evidence_source=source, internship_evidence_value=value))


@pytest.mark.parametrize("source,value", [("liauto:list:job_mode", "202"),
                                         ("feishu:detail:recruit_type.name", "实习")])
def test_confirmed_native_internship_supplies_type_only(source, value):
    candidate = job(requirements="Python", internship_evidence_source=source, internship_evidence_value=value)
    assert not job_condition_reasons("AI Agent 实习", "全国", candidate)
    assert job_condition_reasons("RAG 实习", "上海", candidate)


def test_explicit_duration_requirement_is_internship_evidence():
    assert not job_condition_reasons("AI Agent 实习", "全国", job(requirements="每周至少4天，可实习4个月以上"))


def test_fulltime_query_is_not_changed_by_internship_type_gate():
    assert not job_condition_reasons("AI Agent", "全国", job())


def test_known_fulltime_is_filtered_before_discovery_quota():
    from web_task_agent.search_conditions import candidate_matches

    assert not candidate_matches("AI Agent 实习", "全国", title="AI Agent实习生", jd_text="",
        location="北京", internship_evidence_source="liauto:list:job_mode", internship_evidence_value="101")
