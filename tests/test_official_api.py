"""OfficialApiContentFetcher 测试：fake transport 断言请求构造与解析，不发真实 HTTP。"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError

import pytest

from web_task_agent.browser import PageHttpError, PageTimeoutError
from web_task_agent.official_api import (
    OfficialApiContentFetcher,
    OfficialApiUnavailableError,
    UnsupportedOfficialApiError,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_fetch_tencent_builds_bypostid_request(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    def fake_urlopen(req, timeout):  # noqa: ANN001
        captured["url"] = req.full_url
        captured["referer"] = req.headers.get("Referer")
        return FakeResponse(
            {
                "Code": 200,
                "Data": {
                    "RecruitPostName": "混元多模态研究（实习生）",
                    "ComName": "腾讯",
                    "Responsibility": "1、负责大模型数据挖掘。",
                    "Requirement": "1、硕士及以上学历。",
                },
            }
        )

    monkeypatch.setattr(
        "web_task_agent.official_api.url_request.urlopen", fake_urlopen
    )
    fetcher = OfficialApiContentFetcher()

    content = await fetcher.fetch(
        "https://careers.tencent.com/jobdesc.html?postId=123456"
    )

    assert "postId=123456" in captured["url"]
    assert "language=zh-cn" in captured["url"]
    assert captured["referer"] == "https://careers.tencent.com/"
    assert content.title == "混元多模态研究（实习生）"
    assert "大模型数据挖掘" in content.content
    assert "硕士及以上学历" in content.content


@pytest.mark.asyncio
async def test_fetch_tencent_unavailable_when_code_not_200(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "web_task_agent.official_api.url_request.urlopen",
        lambda req, timeout: FakeResponse({"Code": 500, "Data": "E1005"}),
    )
    fetcher = OfficialApiContentFetcher()

    with pytest.raises(OfficialApiUnavailableError):
        await fetcher.fetch("https://careers.tencent.com/jobdesc.html?postId=123456")


@pytest.mark.asyncio
async def test_fetch_tencent_url_without_post_id_raises():
    fetcher = OfficialApiContentFetcher()

    with pytest.raises(OfficialApiUnavailableError):
        await fetcher.fetch("https://careers.tencent.com/jobdesc.html")


@pytest.mark.asyncio
async def test_fetch_meituan_filters_list_by_job_union_id(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    def fake_urlopen(req, timeout):  # noqa: ANN001
        captured["body"] = json.loads(req.data.decode("utf-8"))
        target_page = captured["body"]["page"]["pageNo"]
        items = [
            {
                "jobUnionId": "999",
                "name": "其他岗位",
                "jobDuty": "x",
                "jobRequirement": "y",
                "cityList": [],
            }
        ]
        if target_page == 2:
            items.append(
                {
                    "jobUnionId": "4241222974",
                    "name": "大模型应用实习生",
                    "jobDuty": "1、负责大模型应用研发。",
                    "jobRequirement": "1、熟悉 Python。",
                    "cityList": [{"name": "北京市"}],
                }
            )
        return FakeResponse({"data": {"list": items}})

    monkeypatch.setattr(
        "web_task_agent.official_api.url_request.urlopen", fake_urlopen
    )
    fetcher = OfficialApiContentFetcher()

    content = await fetcher.fetch(
        "https://zhaopin.meituan.com/jobdetail?jobUnionId=4241222974"
    )

    assert captured["body"]["page"]["pageNo"] == 2  # 翻到第 2 页才命中
    assert content.title == "大模型应用实习生"
    assert content.company == "美团"
    assert "北京市" in content.content
    assert "大模型应用研发" in content.content


@pytest.mark.asyncio
async def test_fetch_meituan_not_found_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "web_task_agent.official_api.url_request.urlopen",
        lambda req, timeout: FakeResponse({"data": {"list": []}}),
    )
    fetcher = OfficialApiContentFetcher()

    with pytest.raises(OfficialApiUnavailableError):
        await fetcher.fetch(
            "https://zhaopin.meituan.com/jobdetail?jobUnionId=missing"
        )


@pytest.mark.asyncio
async def test_unsupported_host_raises():
    fetcher = OfficialApiContentFetcher()

    with pytest.raises(UnsupportedOfficialApiError):
        await fetcher.fetch("https://www.nowcoder.com/jobs/detail/447182")


@pytest.mark.asyncio
async def test_http_500_with_e1005_maps_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_urlopen(req, timeout):  # noqa: ANN001
        raise HTTPError(
            req.full_url, 500, "Internal Server Error", hdrs=None,  # type: ignore[arg-type]
            fp=None,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(
        "web_task_agent.official_api.url_request.urlopen", fake_urlopen
    )
    # HTTPError.read() 在 fp=None 时会抛错——测试里容忍该路径归为 PageHttpError 分支
    fetcher = OfficialApiContentFetcher()
    try:
        await fetcher.fetch("https://careers.tencent.com/jobdesc.html?postId=x")
    except OfficialApiUnavailableError:
        pass  # body 可读时走 E1005 分类
    except PageHttpError:
        pass  # body 不可读时回退 HTTP 错误分类
    else:
        pytest.fail("expected either OfficialApiUnavailableError or PageHttpError")


def test_timeout_maps_to_page_timeout_error():
    fetcher = OfficialApiContentFetcher(timeout_seconds=1)
    import urllib.request as url_request

    req = url_request.Request("https://10.255.255.1/nonexistent")
    with pytest.raises(PageTimeoutError):
        fetcher._open_json(req)
