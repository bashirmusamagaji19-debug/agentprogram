from types import SimpleNamespace

import pytest

from web_task_agent.official_list_families import make_feishu_hire_lister
from web_task_agent.official_list_source import _tencent_campus_lister, _xiaomi_lister


@pytest.mark.asyncio
async def test_tencent_native_identity_and_paging_are_preserved():
    def open_json(method, url, **kwargs):
        items = [{"positionTitle": "AI 实习生", "postId": "native-1", "workCities": "北京"}]
        return {"data": {"positionList": items if kwargs["body"]["pageNo"] == 1 else []}}
    job = (await _tencent_campus_lister(SimpleNamespace(open_json=open_json), 1))[0]
    assert job.source_job_id == "native-1"
    assert job.discovery_url == "https://join.qq.com/api/v1/position/searchPosition"
    assert job.query_context == {"page": 1, "page_size": 50}
    assert job.discovered_at.tzinfo


@pytest.mark.asyncio
async def test_xiaomi_known_native_id_and_discovery_request_are_preserved():
    def open_json(*args, **kwargs):
        return {"data": {"list": [{"id": "mi-1", "title": "AI 实习生", "url": "https://hr.xiaomi.com/jobs/mi-1",
            "cityZhNames": ["北京"], "description": "开发 AI 系统" * 20, "requirement": "Python" * 20}]}}
    job = (await _xiaomi_lister(SimpleNamespace(open_json=open_json), 1))[0]
    assert job.source_job_id == "mi-1"
    assert "searchJobPage" in job.discovery_url
    assert job.query_context["page"] == 1
    assert job.discovered_at.tzinfo


@pytest.mark.asyncio
async def test_family_native_id_and_public_query_context_are_preserved():
    def open_json(*args, **kwargs):
        return {"data": {"job_post_list": [{"id": "family-1", "title": "AI 实习生",
                     "description": "Build AI systems", "requirement": "Python",
                     "city_info": {"name": "北京"}}]}}
    lister = make_feishu_hire_lister(api_url="https://jobs.example.com/api/search",
                                   url_tmpl="https://jobs.example.com/position/{id}", company="Example")
    job = (await lister(SimpleNamespace(open_json=open_json), 1))[0]
    assert job.source_job_id == "family-1"
    assert job.discovery_url == "https://jobs.example.com/api/search"
    assert job.query_context == {"keyword": "", "offset": 0, "limit": 100}
    assert job.discovered_at.tzinfo
