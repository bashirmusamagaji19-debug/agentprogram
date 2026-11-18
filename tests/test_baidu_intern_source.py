from __future__ import annotations

import json
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import pytest

from web_task_agent.browser import PageEmptyError
from web_task_agent.extractor import PageExtractor
from web_task_agent.job_sources import AggregatorPageLoader, DiscoveredJob
from web_task_agent.official_api import OfficialApiContentFetcher
from web_task_agent.official_list_source import OfficialListSource
from web_task_agent.page_reliability import PageRemovedError
from web_task_agent.search_conditions import job_condition_reasons

_LIST_URL = "https://talent.baidu.com/jobs/list?recruitType=INTERN"
_DETAIL_URL = "https://talent.baidu.com/jobs/detail/INTERN/post-1"
_JD = {
    "postId": "post-1",
    "name": "搜索策略工程师",
    "workPlace": "上海市",
    "publishDate": "2026-11-17",
    "updateDate": "2026-11-18",
    "projectType": "日常实习项目",
    "workContent": "用 AI Agent 优化搜索排序，包括搜索质量评估、策略验证和实验数据分析。",
    "serviceCondition": "掌握 Python 和机器学习，能够设计离线评估方法并分析线上实验结果。"
                        "了解字符串 undefined、实验日志和性能指标，具备团队协作能力。",
}


def _page(*, list_data=None, detail_data=None):
    state = json.dumps(
        {"listData": list_data, "detailData": detail_data, "projectType": None},
        ensure_ascii=False,
    ).replace('"projectType": null', '"projectType": undefined')
    return f"<script>window.__USE_SSR__=true; window.__INITIAL_DATA__ ={state}; window.prefix=\"/jobs\";</script>"


@pytest.mark.asyncio
async def test_baidu_search_derives_internship_from_public_list_context():
    class PublicTransport:
        def __init__(self):
            self.calls = []

        def fetch_html(self, url):
            self.calls.append(url)
            query = parse_qs(urlsplit(url).query)
            assert query["recruitType"] == ["INTERN"]
            return _page(list_data={
                "recruitType": "INTERN",
                "pageNum": 1,
                "pageSize": 10,
                "total": 500,
                "listDetailData": [
                    _JD | {"postId": "post-other-city", "workPlace": "北京市"},
                    _JD | {"postId": "post-other-keyword", "workContent": "精细化管理与运营。" * 20},
                    _JD,
                ] if "search" in query else [],
            })

        def open_json(self, *args, **kwargs):
            raise AssertionError("public internship discovery must use the rendered website")

    transport = PublicTransport()
    source = OfficialListSource(
        specs=["baidu"], transport=transport, keyword="AI Agent 实习", location="上海"
    )
    jobs = await source.discover(1)
    assert [job.url for job in jobs] == [_DETAIL_URL]
    job = jobs[0]
    assert job.internship_evidence_source == "baidu:list:recruitType"
    assert job.internship_evidence_value == "INTERN"
    assert job.source_job_id == "post-1"
    assert job.posted_at == "2026-11-17"
    assert "字符串 undefined" in job.jd_text
    assert job.discovery_url in transport.calls
    assert parse_qs(urlsplit(job.discovery_url).query).get("search")
    assert source.diagnostics[0]["status"] == "healthy"


@pytest.mark.asyncio
async def test_baidu_unrecognized_list_is_a_discovery_failure():
    class PublicTransport:
        def fetch_html(self, url):
            return "<div id='root'></div>"

        def open_json(self, *args, **kwargs):
            raise AssertionError("the public adapter must not fall back to the legacy request")

    source = OfficialListSource(specs=["baidu"], transport=PublicTransport(), keyword="Agent 实习")
    assert await source.discover(2) == []
    assert source.diagnostics[0]["status"] == "failed"
    assert source.diagnostics[0]["error_detail"] == "baidu public list data unavailable"


@pytest.mark.asyncio
async def test_baidu_public_detail_is_verified_and_extractable(monkeypatch):
    detail_html = _page(detail_data={"isValid": True, "recruitType": "INTERN", "postInfo": _JD})

    def urlopen(request, *, timeout):
        assert request.full_url == _DETAIL_URL
        assert request.get_method() == "GET"
        assert timeout > 0
        return BytesIO(detail_html.encode("utf-8"))

    monkeypatch.setattr("web_task_agent.official_api.url_request.urlopen", urlopen)
    loader = AggregatorPageLoader(
        [DiscoveredJob(url=_DETAIL_URL, source="baidu")], official_api=OfficialApiContentFetcher()
    )
    page = await loader(_DETAIL_URL)
    job = PageExtractor().extract(page)
    assert job.url == _DETAIL_URL
    assert job.url_verification == "http-fetched"
    assert job.title == "搜索策略工程师"
    assert job.posted_at == "2026-11-17"
    assert job.internship_evidence_source == "baidu:detail:recruitType"
    assert job_condition_reasons("AI Agent 实习", "上海", job) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("html", "error"),
    [
        ("<div id='root'></div>", PageEmptyError),
        (
            _page(detail_data={"isValid": False, "recruitType": "INTERN", "postInfo": _JD}),
            PageRemovedError,
        ),
        (
            _page(detail_data={
                "isValid": True, "recruitType": "INTERN", "postInfo": _JD | {"postId": "other-post"},
            }),
            PageEmptyError,
        ),
    ],
    ids=["shell", "closed", "mismatched-id"],
)
async def test_baidu_public_detail_requires_matching_id_and_online_state(monkeypatch, html, error):
    monkeypatch.setattr(
        "web_task_agent.official_api.url_request.urlopen",
        lambda request, *, timeout: BytesIO(html.encode("utf-8")),
    )
    with pytest.raises(error):
        await OfficialApiContentFetcher().fetch(_DETAIL_URL)
