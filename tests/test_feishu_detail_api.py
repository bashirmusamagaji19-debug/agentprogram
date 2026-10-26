"""Regression tests for registered Feishu native detail URLs."""

from __future__ import annotations

import pytest

from web_task_agent.official_api import (
    OfficialApiContentFetcher,
    OfficialApiUnavailableError,
)
from web_task_agent.page_reliability import PageRemovedError


@pytest.mark.parametrize(
    ("source", "host", "post_id"),
    [
        ("minimax", "vrfi1sk8a0.jobs.feishu.cn", "7669691866238142746"),
        ("shengshu", "shengshu.jobs.feishu.cn", "7673925527993157898"),
    ],
)
@pytest.mark.asyncio
async def test_index_portal_discovery_uses_the_live_detail_route(source, host, post_id):
    from web_task_agent.official_list_source import _SPECS

    class Transport:
        def open_json(self, *args, **kwargs):
            return {"data": {"job_post_list": [_detail_payload(post_id)["data"]["job_post_detail"]]}}

    jobs = await _SPECS[source](Transport(), 1)

    assert len(jobs) == 1
    assert jobs[0].url == f"https://{host}/index/position/{post_id}/detail"


@pytest.mark.parametrize("route", ["position/{id}", "position/detail/{id}", "position/{id}/detail"])
@pytest.mark.parametrize("host", ["vrfi1sk8a0.jobs.feishu.cn", "shengshu.jobs.feishu.cn"])
@pytest.mark.asyncio
async def test_old_index_routes_are_canonicalized_to_the_live_detail_url(monkeypatch, host, route):
    post_id = "7673925527993157898"
    monkeypatch.setattr(OfficialApiContentFetcher, "_open_json", lambda self, req: _detail_payload(post_id))

    content = await OfficialApiContentFetcher().fetch(f"https://{host}/index/{route.format(id=post_id)}")

    assert content.canonical_url == f"https://{host}/index/position/{post_id}/detail"


def _detail_payload(
    post_id: str,
    *,
    title: str = "Agent 算法实习生",
    recruit_type_id: str = "202",
    online_status: object = 1,
    include_online_status: bool = True,
) -> dict:
    data = {
        "id": post_id,
        "title": title,
        "description": "负责 Agent 产品与算法研发。",
        "requirement": "熟悉 Python。",
        "recruit_type": {"id": recruit_type_id, "name": "实习"},
        "city_list": [{"name": "上海"}],
    }
    if include_online_status:
        data["channel_online_status"] = online_status
    return {"code": 0, "data": {"job_post_detail": data}, "message": "ok"}


@pytest.mark.parametrize(
    ("url", "post_id", "company", "website_path", "recruit_type_id"),
    [
        (
            "https://xiaopeng.jobs.feishu.cn/campus/position/7698647256014489898/detail",
            "7698647256014489898",
            "小鹏汽车",
            "campus",
            "202",
        ),
        (
            "https://agirobot.jobs.feishu.cn/internrecruitment/position/7647509445749213481/detail",
            "7647509445749213481",
            "智元机器人",
            "internrecruitment",
            "202",
        ),
        (
            "https://vrfi1sk8a0.jobs.feishu.cn/index/position/7669691866238142746",
            "7669691866238142746",
            "MiniMax",
            "index",
            "301",
        ),
        (
            "https://shengshu.jobs.feishu.cn/index/position/7673925527993157898",
            "7673925527993157898",
            "生数科技",
            "index",
            "301",
        ),
    ],
)
@pytest.mark.asyncio
async def test_registered_feishu_detail_uses_native_api_and_recruit_type_name(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    post_id: str,
    company: str,
    website_path: str,
    recruit_type_id: str,
) -> None:
    captured = []

    def fake_open_json(self, req):  # noqa: ANN001
        captured.append(
            {
                "url": req.full_url,
                "headers": {key.lower(): value for key, value in req.header_items()},
            }
        )
        return _detail_payload(
            post_id,
            title="Agent 产品/算法实习生",
            recruit_type_id=recruit_type_id,
        )

    monkeypatch.setattr(
        "web_task_agent.official_api.OfficialApiContentFetcher._open_json",
        fake_open_json,
    )

    content = await OfficialApiContentFetcher().fetch(url)

    host = url.split("/", 3)[2]
    assert captured == [
        {
            "url": f"https://{host}/api/v1/job/posts/{post_id}",
            "headers": {
                "website-path": website_path,
                "referer": url,
                "user-agent": "Mozilla/5.0",
            },
        }
    ]
    assert content.title == "Agent 产品/算法实习生"
    assert content.company == company
    assert content.canonical_url == f"https://{host}/{website_path}/position/{post_id}/detail"
    assert "工作地点：上海" in content.content
    assert "负责 Agent 产品与算法研发" in content.content
    assert "熟悉 Python" in content.content
    assert content.internship_evidence_source == "feishu:detail:recruit_type.name"
    assert content.internship_evidence_value == "实习"


@pytest.mark.asyncio
async def test_registered_index_position_detail_route_uses_index_website_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_id = "7673925527993157898"
    url = f"https://shengshu.jobs.feishu.cn/index/position/detail/{post_id}"
    captured = []

    def fake_open_json(self, req):  # noqa: ANN001
        captured.append({key.lower(): value for key, value in req.header_items()})
        return _detail_payload(post_id)

    monkeypatch.setattr(
        "web_task_agent.official_api.OfficialApiContentFetcher._open_json",
        fake_open_json,
    )

    content = await OfficialApiContentFetcher().fetch(url)

    assert captured[0]["website-path"] == "index"
    assert content.canonical_url == f"https://shengshu.jobs.feishu.cn/index/position/{post_id}/detail"


@pytest.mark.asyncio
async def test_feishu_detail_identity_mismatch_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://xiaopeng.jobs.feishu.cn/campus/position/7698647256014489898/detail"
    monkeypatch.setattr(
        "web_task_agent.official_api.OfficialApiContentFetcher._open_json",
        lambda self, req: _detail_payload("different-id"),
    )

    with pytest.raises(OfficialApiUnavailableError, match="identity mismatch"):
        await OfficialApiContentFetcher().fetch(url)


@pytest.mark.asyncio
async def test_feishu_detail_offline_status_is_page_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_id = "7647509445749213481"
    url = f"https://agirobot.jobs.feishu.cn/internrecruitment/position/{post_id}/detail"
    monkeypatch.setattr(
        "web_task_agent.official_api.OfficialApiContentFetcher._open_json",
        lambda self, req: _detail_payload(post_id, online_status=0),
    )

    with pytest.raises(PageRemovedError, match="no longer online"):
        await OfficialApiContentFetcher().fetch(url)


@pytest.mark.asyncio
async def test_feishu_detail_missing_online_status_is_not_treated_as_online(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_id = "7669691866238142746"
    url = f"https://vrfi1sk8a0.jobs.feishu.cn/index/position/{post_id}"
    monkeypatch.setattr(
        "web_task_agent.official_api.OfficialApiContentFetcher._open_json",
        lambda self, req: _detail_payload(post_id, include_online_status=False),
    )

    with pytest.raises(OfficialApiUnavailableError, match="online status"):
        await OfficialApiContentFetcher().fetch(url)
