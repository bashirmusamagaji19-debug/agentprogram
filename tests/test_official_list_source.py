"""OfficialListSource 测试:官方列表 API 直连发现(fake transport,不发真实 HTTP)。"""

from __future__ import annotations

import json

import pytest

from web_task_agent.official_list_source import (
    MEITUAN_LIST_KEYWORDS,
    OfficialListSource,
    _tencent_campus_lister,
    _unitree_lister,
)


class FakeTransport:
    """记录请求并按 URL/路径回放预置响应。"""

    def __init__(self, responders: dict) -> None:
        self.responders = responders
        self.calls: list[tuple[str, str]] = []  # (method, url)

    def open_json(self, method: str, url: str, *, body: dict | None = None) -> dict:
        self.calls.append((method, url))
        for pattern, responder in self.responders.items():
            if pattern in url:
                return responder(url, body)
        raise AssertionError(f"unexpected request: {method} {url}")


def _tencent_page_payload(page_no: int, titles: list[str]) -> dict:
    return {
        "status": 0,
        "data": {
            "positionList": [
                {
                    "positionTitle": title,
                    "postId": f"123000{page_no}{idx}",
                    "projectName": "应届毕业生",
                    "recruitLabelName": "应届毕业生",
                    "workCities": "深圳总部 北京",
                    "bgs": "TEG CSIG",
                }
                for idx, title in enumerate(titles)
            ],
            "count": len(titles),
        },
    }


@pytest.mark.asyncio
async def test_tencent_campus_lister_filters_intern_ai_and_paginates():
    pages = {
        1: ["AI全栈工程师", "高级产品经理"],  # 第二条:非 AI → 过滤
        2: ["混元多模态-大模型数据挖掘研究（实习生 青云计划）", "岗位x"],
    }
    transport = FakeTransport(
        {
            "searchPosition": lambda url, body: _tencent_page_payload(
                body.get("pageNo", 1),
                pages.get(body.get("pageNo", 1), []),
            )
        }
    )
    jobs = await _tencent_campus_lister(transport, limit=10)

    titles = [j.title for j in jobs]
    assert "AI全栈工程师" in titles
    assert "混元多模态-大模型数据挖掘研究（实习生 青云计划）" in titles
    assert all("高级产品经理" != t for t in titles)
    # 每条都有 careers 形式的 postId URL(详情链路按 host 路由 + canonical 修复)
    for job in jobs:
        assert job.url.startswith("https://careers.tencent.com/jobdesc.html?postId=")
        assert job.source == "tencent-campus"


@pytest.mark.asyncio
async def test_tencent_campus_lister_stops_at_limit():
    transport = FakeTransport(
        {
            "searchPosition": lambda url, body: _tencent_page_payload(
                body.get("pageNo", 1),
                ["AI工程师", "AI研究员", "大模型算法实习生", "LLM应用实习生"],
            )
        }
    )
    jobs = await _tencent_campus_lister(transport, limit=2)

    assert len(jobs) == 2


@pytest.mark.asyncio
async def test_meituan_lister_uses_configured_keywords():
    transport = FakeTransport(
        {
            "getJobList": lambda url, body: {
                "status": 0,
                "data": {
                    "jobList": [
                        {
                            "jobUnionId": "4241222974",
                            "name": "大模型应用实习生",
                            "jobDuty": "x",
                            "jobRequirement": "y",
                            "cityList": [{"name": "北京市"}],
                        }
                    ],
                    "pageInfo": {"pageNo": 1, "pageSize": 50, "total": 1},
                },
            }
        }
    )
    source = OfficialListSource(specs=["meituan"], transport=transport)
    jobs = await source.discover(limit=5)

    assert len(jobs) == 1
    assert jobs[0].company == "美团"
    assert jobs[0].url == "https://zhaopin.meituan.com/jobdetail?jobUnionId=4241222974"
    # 美团列表自带 jobDuty/jobRequirement → jd_text 兜底可直接用
    assert jobs[0].jd_text


@pytest.mark.asyncio
async def test_composite_source_merges_specs_in_order():
    transport = FakeTransport(
        {
            "searchPosition": lambda url, body: _tencent_page_payload(
                body.get("pageNo", 1),
                ["AI全栈工程师"] if body.get("pageNo", 1) == 1 else [],
            ),
            "getJobList": lambda url, body: {
                "status": 0,
                "data": {
                    "jobList": [
                        {
                            "jobUnionId": "999",
                            "name": "Agent 产品运营实习生",
                            "jobDuty": "d",
                            "jobRequirement": "r",
                            "cityList": [],
                        }
                    ],
                    "pageInfo": {"pageNo": 1, "pageSize": 50, "total": 1},
                },
            },
        }
    )
    source = OfficialListSource(specs=["tencent-campus", "meituan"], transport=transport)
    jobs = await source.discover(limit=10)

    # 腾讯 1 条(page 1 命中,page 2 空)在前,美团 1 条在后(顺序 = spec 顺序)
    assert [j.source for j in jobs] == ["tencent-campus", "meituan"]


def test_meituan_list_keywords_are_non_empty():
    assert MEITUAN_LIST_KEYWORDS
    assert all(isinstance(k, str) and k for k in MEITUAN_LIST_KEYWORDS)


def test_unknown_spec_raises_value_error():
    with pytest.raises(ValueError, match="unknown spec"):
        OfficialListSource(specs=["nope"])


# ── 阶段 3A-2:SaaS 家族与 bespoke 适配器(fake transport 参数化)──

def _fake_transport_for(spec_key: str) -> FakeTransport:
    """按规格 JSON 的 sample_evidence 构造最小可信响应。"""
    if spec_key == "unitree":
        return FakeTransport({
            "website/job/list": lambda url, body: {
                "code": 100,
                "data": {"count": 2, "items": [
                    {"id": "2046857577249112064", "title": "具身智能软件工程师",
                     "duty": "负责具身智能系统研发，涵盖感知、决策与控制全栈算法工程"
                             "落地与部署，参与机器人本体在真实场景中的大规模部署迭代。",
                     "ability": "熟悉 ROS 与强化学习框架，具备扎实的工程能力，"
                                "有大模型部署与机器人系统研发经验者优先考虑。",
                     "cityInfo": "杭州"},
                    {"id": "999", "title": "海外专员", "duty": "x", "ability": "y", "cityInfo": ""},


                ]},
            }
        })
    raise AssertionError(spec_key)


@pytest.mark.asyncio
async def test_unitree_lister_maps_fields_and_filters_non_ai():
    jobs = await _unitree_lister(_fake_transport_for("unitree"), limit=10)

    assert len(jobs) == 1
    assert jobs[0].title == "具身智能软件工程师"
    assert jobs[0].company == "宇树科技"
    assert jobs[0].url == "https://www.unitree.com/cn/position/2046857577249112064"
    assert "具身智能系统研发" in jobs[0].jd_text


def test_registry_exposes_thirteen_specs():
    from web_task_agent.official_list_source import _SPECS

    expected = {
        "tencent-campus", "meituan", "unitree", "xiaomi", "netease",
        "xiaohongshu", "mihoyo", "xpeng", "agibot", "galaxea",
        "robotera", "fourier", "ubtech",
    }
    assert set(_SPECS) == expected


def test_moka_envelope_decrypt_roundtrip():
    """AES 信封解密:密钥随响应自带(necromancer),IV 为 Moka 前端公开常量。"""
    import base64

    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    from web_task_agent.official_list_families import _moka_iv_bytes, decrypt_moka_envelope

    key = b"0123456789abcdef"
    inner = json.dumps({"code": 0, "data": {"jobs": [{"id": "j-1", "title": "x"}]}}).encode("utf-8")
    cipher = AES.new(key, AES.MODE_CBC, _moka_iv_bytes())
    envelope = {
        "data": base64.b64encode(cipher.encrypt(pad(inner, AES.block_size))).decode("ascii"),
        "necromancer": key.decode("ascii"),
    }

    decrypted = decrypt_moka_envelope(envelope["data"], envelope["necromancer"])

    assert decrypted["data"]["jobs"][0]["id"] == "j-1"


def test_streamlit_multiselect_offers_new_sources():
    """Streamlit 发现源多选必须覆盖全部注册的 spec(用户可选)。"""
    import inspect

    from web_task_agent import streamlit_app

    src = inspect.getsource(streamlit_app)
    for spec in ("tencent-campus", "meituan", "unitree", "xiaomi", "netease",
                 "xiaohongshu", "mihoyo", "xpeng", "agibot", "galaxea",
                 "robotera", "fourier", "ubtech"):
        assert f'"{spec}"' in src, f"发现源 {spec} 未出现在 Streamlit UI"
