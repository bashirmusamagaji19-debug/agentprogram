from __future__ import annotations

import json

import pytest

from web_task_agent.browser import BrowserUseClient
from web_task_agent.official_api import OfficialApiContentFetcher, OfficialApiUnavailableError
from web_task_agent.official_list_source import OfficialListSource
from web_task_agent.search_conditions import has_official_internship
from web_task_agent.source_provenance import CandidateEvidence, candidate_id

_DETAIL_URL = "https://career.huawei.com/cn/job-details?advertisementId=30859&positionIntentionId=11354"
_DUTIES = "参与 Agent 智能体的规划、设计、研发和部署，协助优化工具调用、检索和模型推理。"
_REQUIREMENTS = "熟悉 Python 和主流人工智能框架，具备机器学习基础，能够独立完成评测和数据分析。"


def _direction(
    *, direction_id: str = "11354", job_id: str = "95290", city: str = "深圳",
    duties: str = _DUTIES, requirements: str = _REQUIREMENTS,
) -> dict:
    return {
        "positionIntentionId": direction_id,
        "jobId": job_id,
        "positionIntention": "Agent技术",
        "jobPlaceName": city,
        "jobResponsibilities": duties,
        "jobDemand": requirements,
    }


@pytest.mark.asyncio
async def test_huawei_official_discovery_matches_internship_direction_and_city():
    class Transport:
        def open_json(self, method, url, *, body, headers):
            assert method == "POST"
            if "/getJobPage?" in url:
                assert body == {
                    "curPage": 1, "pageSize": 100, "jobType": "CR", "recruitmentType": ["INTERN"],
                }
                return {"status": "SUCCESS", "data": {"pageVO": {"totalPages": 1}, "result": [
                    {
                        "jobName": "AI模型工程师", "advertisementId": 30859, "jobId": 95290,
                        "scenarioName": "实习生", "workPlace": "上海/深圳",
                        "mainBusiness": "请您详见岗位意向中的岗位职责",
                        "jobRequire": "请您详见岗位意向中的岗位要求",
                    },
                    {
                        "jobName": "AI模型工程师", "advertisementId": 30860, "jobId": 95291,
                        "scenarioName": "应届生", "workPlace": "深圳",
                    },
                ]}}
            assert "/getPositionIntentionList?" in url
            assert body == {"jobId": 95290}
            return {"status": "SUCCESS", "data": [
                _direction(direction_id="11353", city="上海"),
                _direction(direction_id="11355", job_id="95291"),
                _direction(direction_id="11356", duties="", requirements=""),
                _direction(),
            ]}

    source = OfficialListSource(
        specs=["huawei"], transport=Transport(), keyword="AI Agent 实习", location="深圳",
    )
    jobs = await source.discover(10)

    assert [job.url for job in jobs] == [_DETAIL_URL]
    job = jobs[0]
    assert job.title == "AI模型工程师"
    assert job.location == "深圳"
    assert _DUTIES in job.jd_text and _REQUIREMENTS in job.jd_text
    assert "请您详见岗位意向" not in job.jd_text
    assert job.discovery_url == "https://career.huawei.com/cn/campus-recruitment-job-list?recruitmentType=INTERN"
    assert job.internship_evidence_source == "huawei:list:scenarioName"
    assert has_official_internship(job.internship_evidence_source, job.internship_evidence_value)
    assert not has_official_internship(job.internship_evidence_source, "应届生")
    evidence = CandidateEvidence(
        candidate_id=candidate_id(job.source, source_job_id=job.source_job_id, requested_url=job.url),
        source_spec=job.source, source_job_id=job.source_job_id, requested_url=job.url,
        discovered_at=job.discovered_at, keyword="AI Agent 实习", location="深圳",
        query_context=job.query_context,
    )
    assert evidence.query_context["recruit_type"] == "INTERN"


@pytest.mark.asyncio
async def test_huawei_detail_fetches_the_exact_public_internship_direction(monkeypatch):
    fetcher = OfficialApiContentFetcher()

    def open_json(req):
        assert req.get_method() == "POST"
        if "/getRecruitmentPositionDetail?" in req.full_url:
            assert json.loads(req.data) == {"advertisementId": "30859"}
            return {"status": "SUCCESS", "data": {
                "advertisementId": 30859,
                "jobId": 95290,
                "jobname": "AI模型工程师",
                "jobCity": "上海/深圳",
                "recruitType": "CR",
                "scenarioName": "实习生",
                "mainBusiness": "请您详见岗位意向中的岗位职责",
                "jobRequire": "请您详见岗位意向中的岗位要求",
            }}
        assert "/getPositionIntentionList?" in req.full_url
        assert json.loads(req.data) == {"jobId": 95290}
        return {"status": "SUCCESS", "data": [
            _direction(direction_id="11353", city="上海", duties="无关方向职责"),
            _direction(duties=f"<p>{_DUTIES}</p>"),
        ]}

    monkeypatch.setattr(fetcher, "_open_json", open_json)
    content = await fetcher.fetch(_DETAIL_URL)

    assert content.canonical_url == _DETAIL_URL
    assert "工作地点：深圳" in content.content
    assert _DUTIES in content.content and _REQUIREMENTS in content.content
    assert "<p>" not in content.content and "无关方向职责" not in content.content
    assert content.internship_evidence_source == "huawei:detail:scenarioName"
    assert has_official_internship(content.internship_evidence_source, content.internship_evidence_value)
    with pytest.raises(OfficialApiUnavailableError, match="direction"):
        await fetcher.fetch(_DETAIL_URL.replace("11354", "12345"))


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled_after_load", [True, False])
async def test_huawei_link_browser_waits_for_the_actual_enabled_apply_button(enabled_after_load):
    apply_probes = 0

    class Page:
        async def get_title(self) -> str:
            return "AI模型工程师"

        async def evaluate(self, script: str):
            nonlocal apply_probes
            if "window.location.href" in script:
                return _DETAIL_URL
            if "getComputedStyle(el)" in script:
                apply_probes += 1
                if enabled_after_load and apply_probes > 1:
                    return [{"tag": "button", "text": "申请", "visible": True, "enabled": True}]
                return []
            return _DUTIES

    class Session:
        async def start(self) -> None:
            pass

        async def new_page(self, url: str) -> Page:
            return Page()

        async def close(self) -> None:
            pass

    browser = BrowserUseClient(
        session_factory=Session, render_wait_seconds=0.25, content_ready=lambda content, _: content == _DUTIES,
    )
    page = await browser.open_url(_DETAIL_URL)

    assert apply_probes > 1
    assert bool(page.metadata["apply_controls"]) is enabled_after_load
