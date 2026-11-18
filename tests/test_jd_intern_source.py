from types import SimpleNamespace

import pytest

from web_task_agent.official_list_source import OfficialListSource
from web_task_agent.search_conditions import job_condition_reasons


class JdCampusTransport:
    def __init__(self):
        self.calls = []

    def open_json(self, method, url, *, body=None):
        self.calls.append((method, url, body))
        if url.endswith("/getProjectList"):
            return {
                "success": True,
                "body": {
                    "projectList": [
                        {
                            "code": "talent",
                            "groupList": [
                                {"planMapList": [{"id": 47, "planName": "TGT-顶尖青年技术天才计划"}]},
                                {"planMapList": [{"id": 55, "planName": "TGT-顶尖青年技术实习生"}]},
                            ],
                        },
                        {
                            "code": "internship",
                            "groupList": [
                                {"planMapList": [{"id": 45, "planName": "JD YOUNG-实习生计划"}]},
                            ],
                        },
                    ],
                },
            }
        if url.endswith("/page?type=present"):
            return {
                "success": True,
                "body": {
                    "totalNumber": 4,
                    "items": [
                        self.position(1, 47, "AI Agent应届岗位"),
                        self.position(2, 55, "大模型研究", work="负责研究多模态大模型。"),
                        self.position(3, 45, "企业级AI智能体模型训练"),
                        self.position(4, None, "AI Agent未标招聘项目"),
                    ],
                },
            }
        return {"success": True, "body": []}

    @staticmethod
    def position(publish_id, plan_id, title, work="负责构建 AI Agent 应用。"):
        return {
            "publishId": publish_id,
            "planId": plan_id,
            "positionName": title,
            "workContent": work * 8,
            "qualification": "熟悉 Python，掌握常见深度学习框架。" * 5,
            "requirementVoList": [{"workCity": "北京"}],
        }


@pytest.mark.asyncio
async def test_jd_uses_current_public_internship_projects_and_verifies_each_result_type():
    transport = JdCampusTransport()
    source = OfficialListSource(specs=["jd"], transport=transport, keyword="AI Agent 实习", location="北京")

    discovered = await source.discover(10)

    assert [job.title for job in discovered] == ["企业级AI智能体模型训练"]
    job = discovered[0]
    assert job.source_job_id == "3"
    assert job.url == "https://campus.jd.com/#/details?id=3"
    assert job.internship_evidence_value == "JD YOUNG-实习生计划"
    assert transport.calls[0][:2] == ("GET", "https://campus.jd.com/api/wx/position/getProjectList")
    assert transport.calls[1][:2] == ("POST", "https://campus.jd.com/api/wx/position/page?type=present")
    assert set(transport.calls[1][2]["parameter"]["planIdList"]) == {45, 55}
    assert not job_condition_reasons(
        "AI Agent 实习",
        "北京",
        SimpleNamespace(
            title=job.title, requirements="熟悉 Python", responsibilities="负责构建 AI Agent 应用",
            skills=[], location=job.location, internship_evidence_source=job.internship_evidence_source,
            internship_evidence_value=job.internship_evidence_value,
        ),
    )
