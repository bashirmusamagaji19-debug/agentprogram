"""OfficialListSource 测试:官方列表 API 直连发现(fake transport,不发真实 HTTP)。"""

from __future__ import annotations

import json

import pytest

from web_task_agent.job_sources import DiscoveredJob
from web_task_agent.official_list_source import (
    _SPECS,
    MEITUAN_LIST_KEYWORDS,
    OfficialListSource,
    _baidu_lister,
    _bing_serp_lister,
    _byd_lister,
    _ctrip_lister,
    _geely_lister,
    _huawei_lister,
    _jd_lister,
    _liauto_lister,
    _nio_lister,
    _pinduoduo_lister,
    _tencent_campus_lister,
    _unitree_lister,
)


class FakeTransport:
    """记录请求并按 URL/路径回放预置响应。"""

    def __init__(self, responders: dict) -> None:
        self.responders = responders
        self.calls: list[tuple[str, str]] = []  # (method, url)

    def open_json(
        self, method: str, url: str, *, body: dict | None = None, form: bool = False
    ) -> dict:
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


def test_registry_exposes_all_specs():
    from web_task_agent.official_list_source import _SPECS

    expected = set([
            "tencent-campus",
            "meituan",
            "unitree",
            "xiaomi",
            "netease",
            "xiaohongshu",
            "mihoyo",
            "xpeng",
            "agibot",
            "galaxea",
            "robotera",
            "fourier",
            "ubtech",
            "jd",
            "pinduoduo",
            "ctrip",
            "huawei",
            "nio",
            "liauto",
            "byd",
            "geely",
            "baidu",
            "bing-serp",
            "zhongqi",
            "xinghai",
            "tarsrobot",
            "x2robot",
            "limx",
            "ai2robotics",
            "astribot",
            "dexmal",
            "booster",
            "deeprobotics",
            "moonshot",
            "zhipu",
            "minimax",
            "stepfun",
            "deepseek",
            "baichuan",
            "modelbest",
            "shengshu",
            "enflame",
    ])
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
    for spec in _SPECS:
        assert f'"{spec}"' in src, f"发现源 {spec} 未出现在 Streamlit UI"


# ── 京东(第二批 agent 产出,实测冒烟通过)──
def _jd_payload(items: list[dict]) -> dict:
    return {
        "success": True,
        "body": [{"deptCode": "", "deptName": None, "channelPositionVoList": items}],
    }


def _jd_item(
    publish_id: int,
    title: str,
    work_content: str = "",
    qualification: str = "",
    cities: tuple = (),
) -> dict:
    return {
        "publishId": publish_id,
        "positionName": title,
        "workContent": work_content,
        "qualification": qualification,
        "requirementVoList": [{"workCity": c} for c in cities],
    }


@pytest.mark.asyncio
async def test_jd_lister_maps_fields_and_filters_non_domain():
    transport = FakeTransport(
        {
            "recommendPage": lambda url, body: _jd_payload(
                [
                    _jd_item(
                        9112,
                        "算法工程师-多模态大模型",
                        work_content="1、负责大模型训练与推理调优" + "x" * 90,
                        qualification="1、硕士及以上学历" + "y" * 90,
                        cities=("北京市-北京市", "北京市-北京市", "广东省-深圳市"),
                    ),
                    _jd_item(9001, "高级产品经理", work_content="z" * 200, qualification="w" * 200),
                    _jd_item(9002, "算法工程师-短JD", work_content="太短", qualification="无正文"),
                    _jd_item(
                        8746,
                        "新一代大模型推理技术优化研究",
                        work_content="a" * 100,
                        qualification="b" * 100,
                    ),
                    _jd_item(
                        9200,
                        "大模型算法实习生",
                        work_content="c" * 100,
                        qualification="d" * 100,
                        cities=("上海市-上海市",),
                    ),
                ]
            )
        }
    )
    jobs = await _jd_lister(transport, limit=10)

    titles = [j.title for j in jobs]
    assert titles == [
        "算法工程师-多模态大模型",
        "新一代大模型推理技术优化研究",
        "大模型算法实习生",  # 不按实习/校招过滤,只按标题域词
    ]
    first = jobs[0]
    assert first.company == "京东"
    assert first.source == "jd"
    assert first.url == "https://campus.jd.com/#/details?id=9112"
    # location 取 requirementVoList[].workCity(顶层 workCity 恒为 null),同名城市去重
    assert first.location == "北京市-北京市、广东省-深圳市"
    assert first.jd_text.startswith("岗位职责：")
    assert "任职要求：" in first.jd_text
    assert all(len(j.jd_text) >= 80 for j in jobs)


@pytest.mark.asyncio
async def test_jd_lister_stops_at_limit_and_single_request():
    transport = FakeTransport(
        {
            "recommendPage": lambda url, body: _jd_payload(
                [
                    _jd_item(
                        9000 + i,
                        f"算法工程师-AI{i}",
                        work_content="a" * 100,
                        qualification="b" * 100,
                    )
                    for i in range(4)
                ]
            )
        }
    )
    jobs = await _jd_lister(transport, limit=2)

    assert len(jobs) == 2
    # pageSize=1000 一次拉全量(分页参数实测无效),不发第二个请求
    assert len(transport.calls) == 1


# ── 拼多多(第二批 agent 产出,实测冒烟通过)──
def _fake_pdd_transport() -> FakeTransport:
    """按实测响应结构构造:列表自带 jobDuty,详情补 serveRequirement/shareUrl。"""

    def list_responder(url, body):
        if body.get("page", 1) == 1:
            return {
                "success": True,
                "result": {
                    "total": 31,
                    "list": [
                        {
                            "id": "43b706c2",
                            "name": "AI Infra研发工程师",
                            "workLocationName": "上海",
                            "jobDuty": "负责大模型训练与推理基础设施研发，"
                            "覆盖集群调度与训练框架优化。",
                        },
                        {  # 非 AI/算法域 → 应被过滤
                            "id": "prod-1",
                            "name": "产品管培生（上海）",
                            "workLocationName": "上海",
                            "jobDuty": "产品管培生轮岗培养。",
                        },
                    ],
                },
            }
        return {"success": True, "result": {"total": 31, "list": []}}

    def detail_responder(url, body):
        if body.get("id") == "43b706c2":
            return {
                "success": True,
                "result": {
                    "jobDuty": "负责大模型训练与推理基础设施研发，"
                            "覆盖集群调度与训练框架优化。",
                    "serveRequirement": "1. 计算机科学、人工智能、数学等相关专业优先；"
                       "2. 扎实的机器学习基础与出色的编程能力。",
                    "shareUrl": "https://careers.pddglobalhr.com/cam"
                        "pus/grad/detail?positionId=43b706c2",
                },
            }
        return {"success": True, "result": {}}

    return FakeTransport({"search/list": list_responder, "position/detail": detail_responder})


@pytest.mark.asyncio
async def test_pinduoduo_lister_maps_fields_and_filters_non_ai():
    jobs = await _pinduoduo_lister(_fake_pdd_transport(), limit=5)

    assert len(jobs) == 1
    assert jobs[0].title == "AI Infra研发工程师"
    assert jobs[0].company == "拼多多"
    assert jobs[0].source == "pinduoduo"
    assert jobs[0].url == "https://careers.pddglobalhr.com/campus/grad/detail?positionId=43b706c2"
    assert jobs[0].location == "上海"
    assert "岗位职责：" in jobs[0].jd_text
    assert "任职要求：" in jobs[0].jd_text
    assert "大模型训练与推理基础设施" in jobs[0].jd_text
    assert "机器学习基础" in jobs[0].jd_text


@pytest.mark.asyncio
async def test_pinduoduo_lister_falls_back_to_list_duty_and_skips_short_body():
    transport = _fake_pdd_transport()

    def list_responder(url, body):
        if body.get("page", 1) != 1:
            return {"success": True, "result": {"total": 31, "list": []}}
        return {
            "success": True,
            "result": {
                "total": 31,
                "list": [
                    {  # 详情接口失败 → 回退列表 jobDuty(超过 80 字符,应保留)
                        "id": "duty-fallback",
                        "name": "大模型算法工程师",
                        "workLocationName": "上海",
                        "jobDuty": "负责电商领域多语言多模态大模型基座研发，"
                       "全流程深入数据处理、样本标注、"
                                   "模型预训练（Pretrain）、有监督微调（SFT）/强化学习（RL）等关键环节；"
                                   "持续跟踪并攻关大模型领域前沿方向，驱动业务高效落地与技术创新。",
                    },
                    {  # 正文过短 → 诚实原则直接跳过
                        "id": "short-body",
                        "name": "算法工程师",
                        "workLocationName": "上海",
                        "jobDuty": "x",
                    },
                ],
            },
        }

    def detail_responder(url, body):
        raise ConnectionError("detail api down")

    transport.responders = {"search/list": list_responder, "position/detail": detail_responder}
    jobs = await _pinduoduo_lister(transport, limit=5)

    assert len(jobs) == 1
    assert jobs[0].title == "大模型算法工程师"
    assert jobs[0].url == (
        "https://careers.pddglobalhr.com/campus/grad/detail?positionId=duty-fallback"
    )
    assert "多模态大模型基座研发" in jobs[0].jd_text
    assert "任职要求：" in jobs[0].jd_text


# ── 携程(第二批 agent 产出,实测冒烟通过)──
def _ctrip_item(from_id: str, title: str, requirements: str, duty=None, city="Shanghai") -> dict:
    return {
        "id": "29918037",
        "fromId": from_id,
        "jobId": "727c8e37-9767-47b0-bbf7-aa01dafbedd5",
        "jobTitle": title,
        "publishDate": "2026-09-07",
        "city": "CO0009",
        "cityName": city,
        "requirements": requirements,
        "duty": duty,
        "kind": "1",
        "category": "2",
        "atsApiType": "Moka",
    }


_LONG_REQUIREMENTS = (
    "<p>招聘对象：本、硕、博。</p><p><br></p><p>你将会负责：</p>"
    "<p>1. 基于大模型与机器学习算法，对旅游场景文本进行深度语义理解；</p>"
    "<p>2. 负责模型训练、评测与线上部署迭代。</p>"
    "<p>任职资格：计算机相关专业，扎实的编码与算法基础。</p>"
)
_EXP_REQUIREMENTS = (
    "<p>岗位职责：负责 AI Agent 平台的架构设计与研发落地，"
    "推动智能体在客服与行程规划场景规模化应用。</p>"
    "<p>任职资格：3 年以上后端研发经验，熟悉大模型推理服务。</p>"
)


def _ctrip_responder(url: str, body: dict | None) -> dict:
    body = body or {}
    condition = body.get("condition") or {}
    keyword = str(condition.get("keyword") or "")
    category = condition.get("category")
    index = (body.get("pager") or {}).get("index", 1)
    if index > 1:  # 翻到第 2 页即空,验证分页终止
        return {"retValue": {"total": 0, "recruitJobAdList": []}}
    if category == 2 and keyword == "大模型":
        return {
            "retCode": "201",
            "retValue": {
                "total": 2,
                "recruitJobAdList": [
                    _ctrip_item("MJ036605", "LLM算法工程师（2027届秋招）", _LONG_REQUIREMENTS),
                    # 非 AI 域词标题 → 应被过滤;jd 再短也无所谓
                    _ctrip_item("MJ036953", "市场营销（2027届秋招）", "<p>x</p>"),
                ],
            },
        }
    if category == 1 and keyword == "AI":
        return {
            "retCode": "201",
            "retValue": {
                "total": 2,
                "recruitJobAdList": [
                    # 社招:duty + requirements 双字段 → 拼接为 岗位职责/任职要求
                    _ctrip_item(
                        "MJ036707",
                        "资深AI Agent开发工程师",
                        _EXP_REQUIREMENTS,
                        duty="<p>1. 主导 Agent 框架研发；</p>",
                    ),
                    # 正文 <80 字符 → 诚实跳过
                    _ctrip_item("MJ036648", "算法工程师实习生（大模型方向）", "<p>短正文</p>"),
                ],
            },
        }
    return {"retValue": {"total": 0, "recruitJobAdList": []}}


@pytest.mark.asyncio
async def test_ctrip_lister_maps_fields_filters_and_splits_routes():
    transport = FakeTransport({"getJobAd": _ctrip_responder})
    jobs = await _ctrip_lister(transport, limit=10)

    assert [j.title for j in jobs] == [
        "LLM算法工程师（2027届秋招）",
        "资深AI Agent开发工程师",
    ]
    # 校招/社招路由按 category 区分,参数是 fromId(MJ 开头)而非数字 id
    assert jobs[0].url == "https://careers.ctrip.com/campus/job-detail/MJ036605"
    assert jobs[1].url == "https://careers.ctrip.com/experienced/job-detail/MJ036707"
    assert all(j.company == "携程" for j in jobs)
    assert all(j.source == "ctrip" for j in jobs)
    assert jobs[0].location == "上海"  # cityName 英文 → 中文映射
    assert jobs[1].location == "上海"
    # HTML 已剥离;duty+requirements → 岗位职责/任职要求 拼接
    assert "<p>" not in jobs[0].jd_text and "</p>" not in jobs[0].jd_text
    assert jobs[0].jd_text.startswith("招聘对象：")
    assert jobs[1].jd_text.startswith("岗位职责：\n1. 主导 Agent 框架研发；\n任职要求：\n")
    # 请求体:POST + UTF-8 中文关键词 + category/pager 正确传递
    assert all(method == "POST" for method, _ in transport.calls)
    assert all(url.endswith("/api/hrrecruit/getJobAd") for _, url in transport.calls)


@pytest.mark.asyncio
async def test_ctrip_lister_stops_at_limit_and_dedupes():
    transport = FakeTransport({"getJobAd": _ctrip_responder})
    jobs = await _ctrip_lister(transport, limit=1)

    assert len(jobs) == 1
    assert jobs[0].title == "LLM算法工程师（2027届秋招）"


# ── 华为(第二批 agent 产出,实测冒烟通过)──


def _huawei_transport() -> FakeTransport:
    def responder(url, body):
        if body.get("curPage", 1) > 1:
            return {"status": "SUCCESS", "data": {"pageVO": {"totalPages": 1}, "result": []}}
        return {
            "status": "SUCCESS",
            "data": {
                "pageVO": {"totalRows": 3, "totalPages": 1},
                "result": [
                    {
                        "advertisementId": "39004",
                        "jobName": "AI辅助办公应用建设专家",
                        "workPlace": "深圳",
                        "mainBusiness": "1、AI办公场景识别与需求洞察，深入理解华为办公业务场景；"
                        "2、设计AI辅助办公产品方案并推动落地。",
                        "jobRequire": "教育背景要求：本科及以上学历；技能要求：熟悉大模型应用落地、"
                      "产品方案设计与跨团队协作。",
                    },
                    {
                        "advertisementId": "36415",
                        "jobName": "AI Infra研究员",
                        "workPlace": "深圳",
                        "mainBusiness": "请您详见岗位意向中的岗位职责",
                        "jobRequire": "请您详见岗位意向中的岗位要求",
                    },
                    {
                        "advertisementId": "39001",
                        "jobName": "财经专员",
                        "workPlace": "深圳",
                        "mainBusiness": "负责财务核算与报表编制，" * 10,
                        "jobRequire": "本科及以上学历。" * 10,
                    },
                ],
            },
        }

    return FakeTransport({"getJobPage": responder})


@pytest.mark.asyncio
async def test_huawei_lister_maps_fields_and_skips_placeholder_and_non_ai():
    transport = _huawei_transport()
    jobs = await _huawei_lister(transport, limit=10)

    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "AI辅助办公应用建设专家"
    assert job.company == "华为"
    assert job.source == "huawei"
    assert job.url == "https://career.huawei.com/cn/job-details?advertisementId=39004"
    assert job.location == "深圳"
    assert job.jd_text.startswith("岗位职责：")
    assert "任职要求：" in job.jd_text
    assert "请您详见岗位意向" not in job.jd_text
    assert len(job.jd_text) >= 80


@pytest.mark.asyncio
async def test_huawei_lister_stops_at_limit_and_paginates_until_empty():
    jobs = await _huawei_lister(_huawei_transport(), limit=1)
    assert len(jobs) == 1

    transport = _huawei_transport()
    await _huawei_lister(transport, limit=10)
    assert len(transport.calls) == 2  # page1 命中后拉 page2(空)即停


# ── 蔚来(第二批 agent 产出,实测冒烟通过)──

_NIO_LONG_DUTY = (
    "负责多模态大模型的算法迭代与应用落地，覆盖模型结构设计、训练与推理优化全流程工作"
)
_NIO_LONG_REQ = "计算机相关专业本科及以上，具备扎实的机器学习基础与大模型训练落地经验者优先"


def _nio_page(items: list[dict]) -> dict:
    return {"code": 0, "data": {"count": len(items), "job_post_list": items}}


def _nio_job_item(
    job_id: str,
    title: str,
    *,
    description: str = "",
    requirement: str = "",
    cities: tuple[str, ...] = ("上海",),
) -> dict:
    return {
        "id": job_id,
        "title": title,
        "description": description,
        "requirement": requirement,
        "city_info": None,
        "city_list": [{"name": c} for c in cities],
    }


@pytest.mark.asyncio
async def test_nio_lister_maps_fields_and_filters_non_domain():
    """蔚来:字段映射 + 非岗位域标题/短正文(<80)过滤(fake transport)。"""
    transport = FakeTransport(
        {
            "search/job/posts": lambda url, body: _nio_page(
                [
                    _nio_job_item(
                        "1001",
                        "大模型-VLM/VLA 多模态算法工程师",
                        description=_NIO_LONG_DUTY,
                        requirement=_NIO_LONG_REQ,
                        cities=("北京", "上海"),
                    ),
                    # 非岗位域标题 → 过滤
                    _nio_job_item(
                        "1002",
                        "门店运营专员",
                        description=_NIO_LONG_DUTY,
                        requirement=_NIO_LONG_REQ,
                    ),
                    # 正文过短(<80)→ 跳过
                    _nio_job_item("1003", "大模型训练框架工程师", description="太短"),
                ]
            )
        }
    )

    jobs = await _nio_lister(transport, limit=10)

    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "大模型-VLM/VLA 多模态算法工程师"
    assert job.company == "蔚来"
    assert job.source == "nio"
    assert job.url == "https://nio.jobs.feishu.cn/index/position/detail/1001"
    assert job.location == "北京、上海"
    assert "岗位职责：" in job.jd_text and _NIO_LONG_DUTY in job.jd_text
    assert "任职要求：" in job.jd_text and _NIO_LONG_REQ in job.jd_text


@pytest.mark.asyncio
async def test_nio_lister_dedupes_across_keywords_and_respects_page_cap():
    """同一岗位命中多个关键词只收一次;全局翻页预算 15 页封顶。"""
    transport = FakeTransport(
        {
            "search/job/posts": lambda url, body: _nio_page(
                [
                    _nio_job_item(
                        "2001",
                        "AI中台工程师",
                        description=_NIO_LONG_DUTY,
                        requirement=_NIO_LONG_REQ,
                    )
                ]
            )
        }
    )

    jobs = await _nio_lister(transport, limit=5)

    assert len(jobs) == 1
    search_calls = [c for c in transport.calls if "search/job/posts" in c[1]]
    assert 1 < len(search_calls) <= 15  # 去重后继续翻关键词,但 15 页封顶


@pytest.mark.asyncio
async def test_nio_lister_stops_at_limit():
    transport = FakeTransport(
        {
            "search/job/posts": lambda url, body: _nio_page(
                [
                    _nio_job_item(
                        f"300{i}",
                        f"大模型算法工程师{i}",
                        description=_NIO_LONG_DUTY,
                        requirement=_NIO_LONG_REQ,
                    )
                    for i in range(4)
                ]
            )
        }
    )

    jobs = await _nio_lister(transport, limit=2)

    assert len(jobs) == 2
    assert len(transport.calls) == 1  # 达到 limit 不再翻页/换关键词


# ── 理想汽车(第二批 agent 产出,实测冒烟通过)──
def _liauto_list_payload(items: list[dict]) -> dict:
    return {"code": 0, "data": {"page": 1, "total_pages": 1, "items": items}}


@pytest.mark.asyncio
async def test_liauto_lister_maps_fields_and_filters_non_ai():
    """社招(social)在前;非域词过滤;详情 HTML 剥离;详情失败跳过;校招通道兜底。"""

    def _list_responder(url: str, body):
        if "social/job-page" in url:
            return _liauto_list_payload(
                [
                    {
                        "id": 19219,
                        "code": "A41245",
                        "title": "【人形机器人】商业化产品经理",
                        "job_mode_name": "全职",
                        "location_title": "北京顺义区",
                    },
                    {
                        # 非岗位域 → 过滤
                        "id": 20228,
                        "code": "A160234",
                        "title": "用户体验运营",
                        "job_mode_name": "全职",
                        "location_title": "北京",
                    },
                ]
            )
        # school/job-page:详情接口失败的岗位(5000)应被跳过
        return _liauto_list_payload(
            [
                {
                    "id": 5000,
                    "code": "A99999",
                    "title": "大模型算法实习生",
                    "job_mode_name": "实习",
                    "location_title": "上海",
                }
            ]
        )

    def _detail_responder(url: str, body):
        job_id = url.split("job_id=")[1]
        if job_id == "5000":
            raise RuntimeError("detail unavailable")
        return {
            "code": 0,
            "data": {
                "title": "【人形机器人】商业化产品经理",
                "description": "<p>【岗位职责】</p><p>1. 深耕人形机器人行业趋势与客户需求调研,"
                ",输出商业化方案。</p>",
                "requirements": "<p>【岗位要求】</p><p>1. 5 年以上 B 端产品或解决方案经验,"
                ",熟悉机器人行业生态。</p>",
            },
        }

    transport = FakeTransport(
        {"job-page": _list_responder, "job/detail": _detail_responder}
    )
    jobs = await _liauto_lister(transport, limit=5)

    # social 页第一条命中 + 第二条被过滤;school 页详情失败被跳过
    assert [j.title for j in jobs] == ["【人形机器人】商业化产品经理"]
    job = jobs[0]
    assert job.url == "https://www.lixiang.com/employ/detail/19219.html"
    assert job.company == "理想汽车"
    assert job.source == "liauto"
    assert job.location == "北京顺义区"
    assert "岗位职责：" in job.jd_text and "任职要求：" in job.jd_text
    assert "<p>" not in job.jd_text  # HTML 已剥离
    assert len(job.jd_text) >= 80


# ── 比亚迪(第二批 agent 产出,实测冒烟通过)──
def _fake_transport_for_byd() -> FakeTransport:
    list_items = [
        {"id": "1802996110122344449", "positionName": "高级算法工程师", "city": "深圳市"},
        {"id": "1803338074273374210", "positionName": "机器人工程师", "city": "西安市"},
        {"id": "888", "positionName": "海外销售专员", "city": "深圳市"},  # 非 AI → 过滤
    ]
    details = {
        "1802996110122344449": [
            {"name": "工作职责", "detail": (
                "1.开发解耦轨迹规划控制算法，实现控制车辆来跟踪运动轨迹；\r\n"
                "2.针对多目标约束进行整车系统选型优化分析，支撑量产车型算法落地与迭代；")},
            {"name": "任职要求", "detail": (
                "硕士及以上学历，车辆工程/计算机/自动化相关专业，"
                "熟悉控制理论与优化方法，有算法落地与量产项目经验者优先。")},
        ],
        "1803338074273374210": [
            {"name": "工作职责", "detail": (
                "1.负责机器人本体运动控制与感知模块的算法研发与工程落地；"
                "2.参与机器人系统在真实场景中的部署调试与迭代优化。")},
            {"name": "任职要求", "detail": (
                "熟悉 ROS 与强化学习框架，具备扎实的工程实现能力，"
                "有机器人系统研发与部署经验者优先。")},
        ],
        "888": [{"name": "工作职责", "detail": "销售"}, {"name": "任职要求", "detail": "沟通"}],
    }
    return FakeTransport({
        "queryList": lambda url, body: {"code": 0, "data": {"data": list_items, "total": 3}},
        "queryDetail": lambda url, body: {
            "code": 0,
            "data": {"tagDetailList": details.get(str((body or {}).get("id")), [])},
        },
    })


@pytest.mark.asyncio
async def test_byd_lister_maps_fields_and_filters_non_ai():
    jobs = await _byd_lister(_fake_transport_for_byd(), limit=10)

    assert len(jobs) == 2
    assert jobs[0].title == "高级算法工程师"
    assert jobs[0].company == "比亚迪"
    assert jobs[0].source == "byd"
    assert jobs[0].location == "深圳市"
    assert jobs[0].url == (
        "https://job.byd.com/portal/pc/#/skiller/skillerPositionDetails?id=1802996110122344449"
    )
    assert "岗位职责：" in jobs[0].jd_text and "任职要求：" in jobs[0].jd_text
    assert all(j.title != "海外销售专员" for j in jobs)


@pytest.mark.asyncio
async def test_byd_lister_stops_at_limit():
    jobs = await _byd_lister(_fake_transport_for_byd(), limit=1)

    assert len(jobs) == 1
    assert len(jobs[0].jd_text) >= 80


@pytest.mark.asyncio
async def test_byd_lister_skips_job_with_short_jd_text():
    transport = FakeTransport({
        "queryList": lambda url, body: {
            "code": 0,
            "data": {"data": [{"id": "777", "positionName": "算法实习生", "city": "合肥市"}]},
        },
        "queryDetail": lambda url, body: {
            "code": 0,
            "data": {"tagDetailList": [{"name": "工作职责", "detail": "打杂"}]},
        },
    })
    jobs = await _byd_lister(transport, limit=5)

    assert jobs == []  # <80 字符正文 → 诚实跳过


# ── 吉利(第二批 agent 产出,实测冒烟通过)──
def _geely_envelope(payload: dict) -> dict:
    """构造 Moka AES 信封(密钥随信封自带,与 decrypt_moka_envelope 互逆)。"""
    import base64

    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    from web_task_agent.official_list_families import _moka_iv_bytes

    key = b"geely-fake-key16"  # 16 字节,与真实 necromancer 同长度
    inner = json.dumps(payload).encode("utf-8")
    cipher = AES.new(key, AES.MODE_CBC, _moka_iv_bytes())
    return {
        "data": base64.b64encode(
            cipher.encrypt(pad(inner, AES.block_size))
        ).decode("ascii"),
        "necromancer": key.decode("ascii"),
    }


@pytest.mark.asyncio
async def test_geely_lister_maps_fields_and_requires_verifiable_jd():
    list_payload = {
        "code": 0,
        "data": {
            "jobs": [
                {
                    "id": "j-1",
                    "title": "大模型算法岗",
                    "locations": [{"cityName": "北京市", "provinceName": "北京市"}],
                    "status": "open",
                },
                {  # 非域词标题 → 过滤,不发详情请求
                    "id": "j-2",
                    "title": "财务专员",
                    "locations": [{"cityName": "宁波市"}],
                },
                {  # 详情失败 → 无可验证正文,跳过
                    "id": "j-3",
                    "title": "机器人算法研究岗",
                    "locations": [{"provinceName": "浙江省"}],
                },
                {  # 正文过短(<80 字符)→ 跳过
                    "id": "j-4",
                    "title": "自动驾驶运营岗",
                    "locations": [{"country": "中国"}],
                },
            ]
        },
    }
    detail_payload = {
        "code": 0,
        "data": {  # campus.geely.com 实测形态:job 字段直接在 data 下
            "title": "大模型算法岗",
            "jobDescription": "<p>1、负责智能座舱语义大模型的研发与迭代优化，"
            "覆盖语义中枢、拒识、任务域指令解析等核心模块；</p>"
            "<p>2、搭建语义模型闭环评估体系，制定科学评估指标，开展模型性能测试与持续优化；</p>"
            "<p>3、负责语义大模型及核心模块的车端适配、轻量化优化与部署落地。</p>",
        },
    }
    short_payload = {"code": 0, "data": {"jobDescription": "职责简述。"}}

    def _detail(url, body):
        if body.get("jobId") == "j-3":
            raise RuntimeError("detail fetch failed")
        return _geely_envelope(short_payload if body.get("jobId") == "j-4" else detail_payload)

    transport = FakeTransport(
        {
            # 注意顺序:列表 URL 含 "jobs/v2",须先于详情 pattern 命中
            "jobs/v2": lambda url, body: _geely_envelope(list_payload),
            "ats-apply/website/job": _detail,
        }
    )
    jobs = await _geely_lister(transport, limit=10)

    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "大模型算法岗"
    assert job.company == "吉利"
    assert job.source == "geely"
    assert job.url == (
        "https://campus.geely.com/campus-recruitment/geely/78436"
        "?locale=zh-CN#/job/j-1"
    )
    assert job.location == "北京市"
    assert "负责智能座舱语义大模型" in job.jd_text
    assert "<" not in job.jd_text  # HTML 已剥离
    assert len(job.jd_text) >= 80


@pytest.mark.asyncio
async def test_geely_lister_paginates_and_stops_at_limit():
    pages = {
        0: [{"id": "a-1", "title": "自动驾驶软件开发岗", "locations": [{"cityName": "杭州市"}]}],
        50: [{"id": "a-2", "title": "大模型算法岗", "locations": [{"cityName": "北京市"}]}],
    }
    detail_payload = {
        "code": 0,
        "data": {"job": {  # Moka 通用形态:data.job 包一层,同样兼容
            "jobDescription": "1、负责智能驾驶应用软件开发；" * 10,
        }},
    }
    list_offsets: list[int] = []

    def _list(url, body):
        list_offsets.append(body.get("offset", 0))
        return _geely_envelope({"code": 0, "data": {"jobs": pages.get(body.get("offset", 0), [])}})

    transport = FakeTransport(
        {
            "jobs/v2": _list,
            "ats-apply/website/job": lambda url, body: _geely_envelope(detail_payload),
        }
    )
    jobs = await _geely_lister(transport, limit=1)

    assert [j.url for j in jobs] == [
        "https://campus.geely.com/campus-recruitment/geely/78436?locale=zh-CN#/job/a-1"
    ]
    assert list_offsets == [0]  # 达到 limit 即停,不再翻页


# ── 阶段 3 收尾:百度(form 表单)+ 3B Bing SERP ──

def _baidu_payload(recruit_type: str, items: list[dict]) -> dict:
    return {"status": "ok", "data": {"total": len(items), "list": items}}


@pytest.mark.asyncio
async def test_baidu_lister_maps_fields_and_stops_on_ratelimit():
    """百度:form 表单请求,列表自带 workContent/serviceCondition;
    限流(status=no-auth)时诚实返回已获取部分。"""
    calls: list[dict] = []

    def responder(url: str, body: dict) -> dict:
        calls.append(body)
        if body["recruitType"] == "INTERN" and body["keyWord"] == "大模型":
            return _baidu_payload("INTERN", [
                {
                    "name": "大模型评测实习生(J94494)",
                    "postId": "eb731a6f-40f7",
                    "workContent": "1、负责大模型效果评测体系建设" + "x" * 80,
                    "serviceCondition": "1、计算机相关专业本科及以上" + "y" * 80,
                    "workPlace": "北京",
                },
                {"name": "前台行政专员", "postId": "other-id",
                 "workContent": "短", "serviceCondition": ""},
            ])
        # 第二个查询即触发限流(冷却型,复核实录)
        return {"status": "no-auth", "message": "illegal-visit"}

    transport = FakeTransport({"getPostListNew": responder})
    jobs = await _baidu_lister(transport, limit=10)

    assert len(jobs) == 1
    assert jobs[0].company == "百度"
    assert jobs[0].source == "baidu"
    expected_url = "https://talent.baidu.com/jobs/detail/INTERN/eb731a6f-40f7"
    assert jobs[0].url == expected_url
    assert "大模型效果评测体系建设" in jobs[0].jd_text
    # form 表单 + keyWord 服务端过滤口径
    assert calls[0]["recruitType"] == "INTERN"
    assert calls[0]["keyWord"] == "大模型"


def test_fake_transport_signature_supports_form():
    """FakeTransport.open_json 必须与 DefaultTransport 同签名(form 参数)。"""
    import inspect

    sig = inspect.signature(FakeTransport.open_json)
    assert "form" in sig.parameters


@pytest.mark.asyncio
async def test_bing_serp_lister_extracts_and_dedupes():
    """3B:SERP HTML → discover_job_links → DiscoveredJob;跨查询去重。"""

    serp_html = (
        '<li class="b_algo"><h2><a href="https://careers.tencent.com/jobdesc.html?postId=1">a</a></h2></li>'
        '<li class="b_algo"><h2><a href="https://hr.163.com/job-detail.html?id=65260">b</a></h2></li>'
        '<li class="b_algo"><h2><a href="https://www.example.com/other">c</a></h2></li>'
    )

    class FakeHtmlTransport:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def fetch_html(self, url: str) -> str:
            self.queries.append(url)
            return serp_html

        def open_json(self, method: str, url: str, *, body=None, form: bool = False) -> dict:
            raise AssertionError("serp lister must not call open_json")

    transport = FakeHtmlTransport()
    jobs = await _bing_serp_lister(transport, limit=10)

    assert len(jobs) == 2  # example.com 被域过滤;两个查询同结果去重
    assert jobs[0].url == "https://careers.tencent.com/jobdesc.html?postId=1"
    assert jobs[0].source == "bing-serp"
    assert len(transport.queries) >= 2


def test_registry_now_has_22_specs():
    from web_task_agent.official_list_source import _SPECS

    assert {"baidu", "bing-serp"} <= set(_SPECS)
    assert len(_SPECS) == 42


# ── 公平采样(discover 交错合并)──

@pytest.mark.asyncio
async def test_discover_interleaves_sources_for_breadth(monkeypatch):
    """顺序填充会让第一个源吃掉全部名额;公平采样按源预算+轮转交错,
    保证跨梯队广度(全源搜索模式的产品要求)。"""
    import web_task_agent.official_list_source as ols

    async def fake_lister_a(transport, limit):
        return [
            DiscoveredJob(url=f"https://a.example.com/{i}", title=f"A{i}", source="a", tier="大厂")
            for i in range(limit)
        ]

    async def fake_lister_b(transport, limit):
        return [
            DiscoveredJob(
                url=f"https://b.example.com/{i}", title=f"B{i}", source="b", tier="具身智能"
            )
            for i in range(limit)
        ]

    fake_specs = {"a": fake_lister_a, "b": fake_lister_b}
    monkeypatch.setattr(ols, "_SPECS", fake_specs)

    source = ols.OfficialListSource(specs=["a", "b"], transport=object())
    jobs = await source.discover(limit=4)

    sources = [j.source for j in jobs]
    assert sources == ["a", "b", "a", "b"]  # 交错,而非 a,a,a,a
    assert {j.tier for j in jobs} == {"大厂", "具身智能"}


@pytest.mark.asyncio
async def test_discover_handles_empty_sources(monkeypatch):
    import web_task_agent.official_list_source as ols

    async def fake_empty(transport, limit):
        return []

    async def fake_ok(transport, limit):
        return [
            DiscoveredJob(url=f"https://c.example.com/{i}", title=f"C{i}", source="c")
            for i in range(limit)
        ]

    monkeypatch.setattr(ols, "_SPECS", {"empty": fake_empty, "ok": fake_ok})
    source = ols.OfficialListSource(specs=["empty", "ok"], transport=object())
    jobs = await source.discover(limit=3)

    assert len(jobs) == 3


@pytest.mark.asyncio
async def test_discover_isolates_failing_source(monkeypatch):
    """单源故障(限流/SSL)不炸全局搜索 — 其他源正常产出(云端实录)。"""
    import web_task_agent.official_list_source as ols

    async def fake_boom(transport, limit):
        raise RuntimeError("simulated SSL EOF")

    async def fake_ok(transport, limit):
        return [DiscoveredJob(url="https://ok.example.com/1", title="OK岗", source="ok")]

    monkeypatch.setattr(ols, "_SPECS", {"boom": fake_boom, "ok": fake_ok})
    source = ols.OfficialListSource(specs=["boom", "ok"], transport=object())
    jobs = await source.discover(limit=5)

    assert len(jobs) == 1
    assert jobs[0].source == "ok"
