import pytest

from web_task_agent.search_conditions import missing_keywords, parse_keywords


def test_every_keyword_is_required_and_separators_are_normalized():
    assert parse_keywords("ＲＡＧ， Python  RAG") == ["rag", "python"]
    assert missing_keywords("RAG Python", "RAG Agent 实习生，Java") == ["python"]
    assert missing_keywords("RAG Python", "RAG Agent 实习生，使用 Python") == []


def test_ascii_terms_have_boundaries_and_clear_aliases_are_supported():
    assert missing_keywords("AI", "负责 email 与 domain 开发") == ["ai"]
    assert missing_keywords("AI Agent 实习", "人工智能实习生，开发智能体") == []
    assert missing_keywords("Agent 实习", "Build browser agents as an intern") == []


@pytest.mark.parametrize("word", ["RAG", "Python", "多模态"])
def test_unrelated_keywords_cannot_pass(word):
    assert missing_keywords(word, "Java 后端开发实习生")


@pytest.mark.parametrize(('value', 'allowed'), [('202', True), ('2', False), ('', False)])
def test_only_verified_native_intern_type_can_supply_missing_title_keyword(value, allowed):
    from web_task_agent.extractor import PageExtractor
    from web_task_agent.models import BrowserPage
    from web_task_agent.search_conditions import job_condition_reasons

    page = BrowserPage(
        url='https://xiaomi.jobs.f.mioffice.cn/internship/position/123/detail',
        title='AI Agent工程师', source='official-api',
        content='公司：小米\n工作地点：武汉\n岗位职责：开发 AI Agent\n任职要求：Python',
        metadata={'internship_evidence_source': 'xiaomi:detail:recruit_type.id',
                  'internship_evidence_value': value},
    )
    job = PageExtractor().extract(page)
    reasons = job_condition_reasons('AI Agent 实习', '全国', job)
    assert (not reasons) == allowed
    assert job_condition_reasons('AI Agent 实习 RAG', '北京', job)


def test_campus_requirement_mentioning_intern_experience_is_not_an_intern_position():
    from web_task_agent.models import JobPosting
    from web_task_agent.search_conditions import job_condition_reasons

    job = JobPosting(title='AI Agent工程师', company='小米', location='武汉', source='official-api',
                     url='https://example.com/jobs/1', requirements='Python，有实习经验优先',
                     internship_evidence_source='xiaomi:detail:recruit_type.id', internship_evidence_value='2')
    assert job_condition_reasons('AI Agent 实习', '全国', job)
