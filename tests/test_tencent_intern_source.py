import pytest

from web_task_agent.official_api import OfficialApiContentFetcher, OfficialApiUnavailableError
from web_task_agent.official_list_source import OfficialListSource
from web_task_agent.search_conditions import has_official_internship


class TencentCampusTransport:
    def __init__(self):
        self.calls = []

    def open_json(self, method, url, *, body=None):
        self.calls.append((method, url, body))
        if url.endswith("/getProjectMapping"):
            return {
                "status": 0,
                "data": [
                    {"subProjectList": [
                        {"mappingId": 1, "projectId": "1", "projectName": "2027校园招聘"},
                        {"mappingId": 2, "projectId": "2", "projectName": "应届实习"},
                        {"mappingId": 104, "projectId": "4,12", "projectName": "日常实习"},
                        {"mappingId": 20, "projectId": "20", "projectName": "青云计划-实习生"},
                    ]},
                ],
            }
        if not body or "pageIndex" not in body:
            return {"status": 0, "data": {"positionList": [], "count": 0}}
        return {"status": 0, "data": {"count": 2, "positionList": [
            {"postId": "100", "positionTitle": "AI Agent工程师", "projectId": 1,
             "projectName": "2027校园招聘"},
            {"postId": "101", "positionTitle": "多模态agentic模型研究", "projectId": 20,
             "projectName": "青云计划-实习生", "workCities": "北京 上海"},
        ]}}


@pytest.mark.asyncio
async def test_tencent_uses_public_project_mapping_and_searches_only_intern_positions():
    transport = TencentCampusTransport()
    source = OfficialListSource(
        specs=["tencent-campus"], transport=transport, keyword="AI Agent 实习", location="全国",
    )

    jobs = await source.discover(10)

    assert [job.source_job_id for job in jobs] == ["101"]
    assert jobs[0].url == "https://join.qq.com/post_detail.html?postId=101"
    assert transport.calls[0][:2] == ("GET", "https://join.qq.com/api/v1/position/getProjectMapping")
    request = transport.calls[1][2]
    assert request["projectMappingIdList"] == [2, 104, 20]
    assert request["pageIndex"] == 1
    assert request["keyword"] == "agent"


@pytest.mark.asyncio
async def test_tencent_public_detail_promotes_only_the_authoritative_internship_type(monkeypatch):
    api = OfficialApiContentFetcher()
    record = {
        "postId": "101", "title": "多模态模型研究", "workCityList": ["北京"],
        "desc": "构建多模态 AI Agent。", "request": "熟悉 Python 和深度学习。",
        "recruitType": 2,
    }
    monkeypatch.setattr(api, "_get_json", lambda *args, **kwargs: {"status": 0, "data": record})

    detail = await api.fetch("https://join.qq.com/post_detail.html?postid=101")

    assert detail.internship_evidence_value == "2"
    assert has_official_internship(detail.internship_evidence_source, detail.internship_evidence_value)
    record["recruitType"] = 1
    graduate = await api.fetch("https://join.qq.com/post_detail.html?postId=101")
    assert graduate.internship_evidence_value == "1"
    assert not has_official_internship(graduate.internship_evidence_source, graduate.internship_evidence_value)
    record["postId"] = "102"
    with pytest.raises(OfficialApiUnavailableError):
        await api.fetch("https://join.qq.com/post_detail.html?postId=101")
