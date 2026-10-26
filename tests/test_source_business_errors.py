"""Regression tests for official-list business responses and source evidence."""

from __future__ import annotations

import json

import pytest

from web_task_agent.browser import PageHttpError
from web_task_agent.job_sources import DiscoveredJob
from web_task_agent.official_list_source import OfficialListSource, _liauto_lister


class _Transport:
    def __init__(self, responder):
        self.responder = responder
        self.calls: list[tuple[str, str, dict | None]] = []

    def open_json(self, method: str, url: str, *, body=None, **kwargs):
        self.calls.append((method, url, body))
        return self.responder(url, body)


@pytest.mark.asyncio
async def test_meituan_parameter_rejection_is_a_failed_source_diagnostic():
    transport = _Transport(
        lambda url, body: {
            "data": None,
            "status": 0,
            "message": "请求参数格式不正确！",
            "debug_token": "must-not-leak",
        }
    )

    source = OfficialListSource(specs=["meituan"], transport=transport)
    assert await source.discover(1) == []

    diagnostic = source.diagnostics[0]
    assert diagnostic["status"] == "failed"
    assert "upstream rejected request parameters" in diagnostic["error_detail"]
    assert "must-not-leak" not in json.dumps(diagnostic)


@pytest.mark.asyncio
async def test_baidu_access_denial_is_blocked_instead_of_empty():
    transport = _Transport(
        lambda url, body: {
            "status": "no-auth",
            "message": "illegal-visit",
            "debug_token": "must-not-leak",
        }
    )

    source = OfficialListSource(specs=["baidu"], transport=transport)
    assert await source.discover(1) == []

    diagnostic = source.diagnostics[0]
    assert diagnostic["status"] == "failed"
    assert diagnostic["error_category"] == "blocked"
    assert diagnostic["error_detail"] == "upstream denied access (status=no-auth, message=illegal-visit)"
    assert "must-not-leak" not in json.dumps(diagnostic)


@pytest.mark.asyncio
async def test_baidu_access_denial_after_results_is_partial():
    def responder(url: str, body: dict):
        if body["keyWord"] == "大模型":
            return {
                "status": "ok",
                "data": {
                    "list": [
                        {
                            "name": "大模型评测实习生",
                            "postId": "post-1",
                            "workContent": "负责大模型评测。" * 20,
                            "serviceCondition": "计算机相关专业。" * 20,
                            "workPlace": "北京",
                        }
                    ]
                },
            }
        return {"status": "no-auth", "message": "illegal-visit"}

    source = OfficialListSource(specs=["baidu"], transport=_Transport(responder))
    jobs = await source.discover(5)

    assert len(jobs) == 1
    diagnostic = source.diagnostics[0]
    assert diagnostic["status"] == "partial"
    assert diagnostic["count"] == 1
    assert diagnostic["error_category"] == "blocked"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("spec", "response", "category"),
    [
        (
            "meituan",
            {"data": None, "status": 0, "message": "请求参数格式不正确！"},
            "config",
        ),
        (
            "baidu",
            {"status": "no-auth", "message": "illegal-visit"},
            "blocked",
        ),
    ],
)
async def test_permanent_business_rejection_is_not_rescanned(spec, response, category):
    transport = _Transport(lambda url, body: response)
    source = OfficialListSource(specs=[spec], transport=transport)

    assert await source.discover(3) == []
    assert len(transport.calls) == 1
    assert source.diagnostics[0]["attempts"] == 1
    assert source.diagnostics[0]["error_category"] == category


@pytest.mark.asyncio
async def test_generic_http_block_preserves_existing_refill_semantics():
    attempts = 0

    def responder(url: str, body):
        nonlocal attempts
        attempts += 1
        raise PageHttpError("rate limited", status_code=429)

    source = OfficialListSource(specs=["meituan"], transport=_Transport(responder))
    assert await source.discover(1) == []
    assert attempts == 3
    assert source.diagnostics[0]["attempts"] == 3


@pytest.mark.asyncio
async def test_exhausted_source_is_not_rescanned_but_full_budget_source_expands(monkeypatch):
    import web_task_agent.official_list_source as ols

    expand_budgets: list[int] = []
    empty_budgets: list[int] = []
    offset = 0

    async def expanding(transport, budget):
        nonlocal offset
        expand_budgets.append(budget)
        jobs = [
            DiscoveredJob(
                url=f"https://expand.example.com/{index}",
                title=f"大模型岗位 {index}",
            )
            for index in range(offset, min(offset + budget, 6))
        ]
        offset += len(jobs)
        return jobs

    async def exhausted(transport, budget):
        empty_budgets.append(budget)
        return []

    monkeypatch.setitem(ols._SPECS, "test-expanding", expanding)
    monkeypatch.setitem(ols._SPECS, "test-exhausted", exhausted)
    source = ols.OfficialListSource(
        specs=["test-expanding", "test-exhausted"], transport=object()
    )

    assert len(await source.discover(3)) == 3
    assert expand_budgets == [2, 6]
    assert empty_budgets == [2]


@pytest.mark.asyncio
async def test_meituan_uses_the_verified_request_shape_and_list_field():
    def responder(url: str, body: dict):
        return {
            "status": "success",
            "data": {
                "list": [
                    {
                        "jobUnionId": "4241222974",
                        "name": "大模型应用实习生",
                        "jobDuty": "参与大模型应用开发。",
                        "jobRequirement": "熟悉 Python。",
                        "cityList": [{"name": "北京市"}],
                    }
                ]
            },
        }

    transport = _Transport(responder)
    source = OfficialListSource(specs=["meituan"], transport=transport)
    jobs = await source.discover(1)

    assert len(jobs) == 1
    assert jobs[0].url.endswith("jobUnionId=4241222974")
    request = transport.calls[0][2]
    assert request["page"] == {"pageNo": 1, "pageSize": 20}
    assert request["jobShareType"] == "1"
    assert isinstance(request["keywords"], str)
    assert request["cityIdList"] == []
    assert request["jobTypeList"] == []


@pytest.mark.asyncio
async def test_liauto_preserves_raw_job_mode_for_parent_filtering():
    def list_responder(url: str, body):
        if "social/job-page" not in url:
            return {"code": 0, "data": {"items": []}}
        return {
            "code": 0,
            "data": {
                "items": [
                    {"id": 101, "title": "大模型算法工程师", "job_mode": "101"},
                    {"id": 202, "title": "大模型算法实习生", "job_mode": "202"},
                ]
            },
        }

    def detail_responder(url: str, body):
        return {
            "code": 0,
            "data": {
                "description": "<p>" + "岗位职责。" * 20 + "</p>",
                "requirements": "<p>" + "任职要求。" * 20 + "</p>",
            },
        }

    jobs = await _liauto_lister(
        _Transport(
            lambda url, body: detail_responder(url, body)
            if "job/detail" in url
            else list_responder(url, body)
        ),
        limit=2,
    )

    assert [(job.internship_evidence_source, job.internship_evidence_value) for job in jobs] == [
        ("liauto:list:job_mode", "101"),
        ("liauto:list:job_mode", "202"),
    ]
