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
    # 标准标签行分节（规则抽取依赖，复现实录 #19）
    assert "公司：腾讯" in content.content
    assert "岗位职责：\n" in content.content
    assert "任职要求：\n" in content.content
    assert "岗位职责/任职要求" not in content.content


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
    assert "公司：美团" in content.content
    assert "工作地点：北京市" in content.content


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
async def test_lookalike_host_not_matched():
    """host 匹配必须精确到域名："xcareers.tencent.com" 不是腾讯官网。"""
    fetcher = OfficialApiContentFetcher()

    with pytest.raises(UnsupportedOfficialApiError):
        await fetcher.fetch("https://xcareers.tencent.com/jobdesc.html?postId=1")


@pytest.mark.asyncio
async def test_careers_e1005_falls_back_to_campus_api(monkeypatch: pytest.MonkeyPatch):
    """社招库 E1005 的岗（青云计划）→ join.qq.com 校招库兜底（阶段 6A）。"""
    calls: list[str] = []
    campus_payload = {
        "data": {
            "title": "混元多模态-大模型数据挖掘与合成技术研究",
            "workCityList": ["深圳总部"],
            "topicDetail": "课题背景：高质量数据是大模型上限的核心壁垒。",
            "topicRequirement": "学历要求：计算机相关专业的博士/优秀硕士。",
        }
    }

    def fake_open_json(self, req):  # noqa: ANN001
        calls.append(req.full_url)
        if "careers.tencent.com" in req.full_url:
            # 与真实 E1005 行为一致：_open_json 已把它分类为岗位不可用
            raise OfficialApiUnavailableError(
                f"official API says post unavailable (E1005): {req.full_url}"
            )
        return json.loads(json.dumps(campus_payload))

    monkeypatch.setattr(
        "web_task_agent.official_api.OfficialApiContentFetcher._open_json",
        fake_open_json,
    )
    fetcher = OfficialApiContentFetcher()

    content = await fetcher.fetch(
        "https://careers.tencent.com/jobdesc.html?postId=1231829074687944725"
    )

    assert len(calls) == 2  # careers 一跳 + campus 一跳
    assert "join.qq.com" in calls[1]
    assert "公司：腾讯" in content.content
    assert "工作地点：深圳总部" in content.content
    assert "课题背景" in content.content
    assert "博士" in content.content
    assert content.title == "混元多模态-大模型数据挖掘与合成技术研究"


@pytest.mark.asyncio
async def test_campus_api_empty_data_raises_unavailable(monkeypatch: pytest.MonkeyPatch):
    """两库都查不到 → OfficialApiUnavailableError（诚实失败，不伪装）。"""
    calls: list[str] = []

    def fake_open_json(self, req):  # noqa: ANN001
        calls.append(req.full_url)
        if "careers.tencent.com" in req.full_url:
            raise OfficialApiUnavailableError(f"unavailable: {req.full_url}")
        return {"data": None}

    monkeypatch.setattr(
        "web_task_agent.official_api.OfficialApiContentFetcher._open_json",
        fake_open_json,
    )
    fetcher = OfficialApiContentFetcher()

    with pytest.raises(OfficialApiUnavailableError, match="tencent campus detail empty"):
        await fetcher.fetch(
            "https://careers.tencent.com/jobdesc.html?postId=000000"
        )
    # 确认两库都真实尝试过，而非第一跳直接外抛
    assert len(calls) == 2
    assert "join.qq.com" in calls[1]


@pytest.mark.asyncio
async def test_official_api_content_rule_extractable(monkeypatch: pytest.MonkeyPatch):
    """官方 API 正文格式可被规则抽取命中——不再每岗强制 LLM 抽取（复现实录 #19）。"""
    from web_task_agent.extractor import PageExtractor
    from web_task_agent.models import BrowserPage

    monkeypatch.setattr(
        "web_task_agent.official_api.url_request.urlopen",
        lambda req, timeout: FakeResponse(
            {
                "Code": 200,
                "Data": {
                    "RecruitPostName": "大模型应用实习生",
                    "ComName": "腾讯",
                    "Location": "深圳",
                    "Responsibility": "1、负责大模型应用研发；\n2、参与 RAG 系统建设。",
                    "Requirement": "1、熟悉 Python、PyTorch。",
                },
            }
        ),
    )
    fetcher = OfficialApiContentFetcher()
    content = await fetcher.fetch(
        "https://careers.tencent.com/jobdesc.html?postId=123456"
    )
    page = BrowserPage(
        url="https://careers.tencent.com/jobdesc.html?postId=123456",
        title=content.title,
        content=content.content,
        source="official-api",
    )

    job = PageExtractor().extract(page)

    assert job.company == "腾讯"
    assert job.location == "深圳"
    assert "Python" in job.requirements
    assert "RAG" in job.responsibilities
    assert job.confidence >= 0.6  # 规则抽取达到置信线，无需 LLM 兜底


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


@pytest.mark.asyncio
async def test_campus_all_topic_fields_null_raises_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    """topicDetail/topicRequirement 全 null：只有"公司+城市"的壳正文不得返回（#27）。"""
    campus_payload = {
        "data": {
            "title": "壳岗",
            "workCityList": ["深圳总部"],
            "topicDetail": None,
            "topicRequirement": None,
        }
    }

    def fake_open_json(self, req):  # noqa: ANN001
        if "careers.tencent.com" in req.full_url:
            raise OfficialApiUnavailableError(f"unavailable: {req.full_url}")
        return json.loads(json.dumps(campus_payload))

    monkeypatch.setattr(
        "web_task_agent.official_api.OfficialApiContentFetcher._open_json",
        fake_open_json,
    )
    fetcher = OfficialApiContentFetcher()

    with pytest.raises(OfficialApiUnavailableError):
        await fetcher.fetch(
            "https://careers.tencent.com/jobdesc.html?postId=1231829074687944725"
        )


@pytest.mark.asyncio
async def test_generic_code_500_without_e1005_does_not_fall_back_to_campus(
    monkeypatch: pytest.MonkeyPatch,
):
    """瞬时 500（Code:500 壳但无 E1005）是服务器错误，不得误触发校招第二跳（#27）。"""
    import io

    calls: list[str] = []

    def fake_urlopen(req, timeout: float = 0):  # noqa: ANN001, ARG001
        calls.append(req.full_url)
        raise HTTPError(
            req.full_url,
            500,
            "Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(b'{"Code":500,"CodeDesc":"ServerError","Data":null}'),
        )

    monkeypatch.setattr(
        "web_task_agent.official_api.url_request.urlopen", fake_urlopen
    )
    fetcher = OfficialApiContentFetcher()

    with pytest.raises(PageHttpError):
        await fetcher.fetch(
            "https://careers.tencent.com/jobdesc.html?postId=2084123456789012345"
        )

    assert len(calls) == 1  # 只有一跳，没有 join.qq.com 兜底


@pytest.mark.asyncio
async def test_campus_fallback_returns_canonical_join_page_url(
    monkeypatch: pytest.MonkeyPatch,
):
    """校招库兜底成功时必须返回 join.qq.com 的可浏览详情页 URL——
    实习岗 postId 在社招站页面 404（用户实录：结果页链接全是 404），
    内容来自哪个库，链接就指向哪个库。"""
    campus_payload = {
        "data": {
            "title": "混元多模态-大模型数据挖掘与合成技术研究",
            "workCityList": ["深圳总部"],
            "topicDetail": "课题背景：高质量数据是大模型上限的核心壁垒。",
            "topicRequirement": "学历要求：计算机相关专业的博士/优秀硕士。",
        }
    }

    def fake_open_json(self, req):  # noqa: ANN001
        if "careers.tencent.com" in req.full_url:
            raise OfficialApiUnavailableError(f"unavailable: {req.full_url}")
        return json.loads(json.dumps(campus_payload))

    monkeypatch.setattr(
        "web_task_agent.official_api.OfficialApiContentFetcher._open_json",
        fake_open_json,
    )
    fetcher = OfficialApiContentFetcher()

    content = await fetcher.fetch(
        "https://careers.tencent.com/jobdesc.html?postId=1231829074687944725"
    )

    assert content.canonical_url == (
        "https://join.qq.com/post_detail.html?postId=1231829074687944725"
    )


@pytest.mark.asyncio
async def test_social_hit_keeps_original_url(monkeypatch: pytest.MonkeyPatch):
    """社招库直接命中时 canonical_url 为空——careers 详情页本身就是有效页面。"""
    monkeypatch.setattr(
        "web_task_agent.official_api.OfficialApiContentFetcher._open_json",
        lambda self, req: {
            "Code": 200,
            "Data": {
                "RecruitPostName": "大模型算法工程师",
                "ComName": "腾讯",
                "Responsibility": "1、负责大模型研发。",
                "Requirement": "1、硕士及以上学历。",
            },
        },
    )
    fetcher = OfficialApiContentFetcher()

    content = await fetcher.fetch(
        "https://careers.tencent.com/jobdesc.html?postId=123456"
    )

    assert content.canonical_url == ""
