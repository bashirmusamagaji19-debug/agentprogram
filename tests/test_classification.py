"""双维度分类测试:公司梯队(tier)× 岗位类型(category)。"""

from __future__ import annotations

import pytest

from web_task_agent.job_sources import DiscoveredJob, classify_company_tier
from web_task_agent.keywords import classify_job_category


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("大模型算法实习生", "算法"),
        ("混元多模态-大模型数据挖掘与合成技术研究", "算法"),
        ("AI全栈工程师", "工程"),
        ("Agent开发工程师", "工程"),
        ("AI产品策划实习生(游戏AI 竞技机器人方向)", "产品"),
        ("Agent 产品运营实习生", "运营"),
        ("数据产品经理", "产品"),
        ("视觉设计师", "设计"),
        ("高级系统开发工程师", "工程"),
    ],
)
def test_classify_job_category(title: str, expected: str) -> None:
    assert classify_job_category(title) == expected


def test_classify_job_category_fallback() -> None:
    assert classify_job_category("海外市场专员") == "运营"
    assert classify_job_category("") == "其他"


@pytest.mark.parametrize(
    ("company", "expected"),
    [
        ("腾讯", "大厂"),
        ("美团", "大厂"),
        ("理想汽车", "车企"),
        ("宇树科技", "具身智能"),
        ("智元机器人", "具身智能"),
        ("月之暗面", "AI 中厂"),
        ("某不知名小公司", "中小厂/长尾"),
    ],
)
def test_classify_company_tier(company: str, expected: str) -> None:
    assert classify_company_tier(company) == expected


def test_discovered_job_has_tier_field() -> None:
    job = DiscoveredJob(url="https://example.com/x", title="t", tier="大厂")

    assert job.tier == "大厂"


def test_official_list_source_backfills_tier() -> None:
    """discover() 按 spec 名从 _SPEC_TIER 回填 tier,liser 自身无需感知。"""
    import asyncio

    from web_task_agent.official_list_source import OfficialListSource

    class OneJobTransport:
        def open_json(self, method: str, url: str, *, body=None, form: bool = False) -> dict:
            return {
                "status": 0,
                "data": {
                    "positionList": [
                        {
                            "positionTitle": "AI全栈工程师",
                            "postId": "p-1",
                            "projectName": "应届毕业生",
                            "recruitLabelName": "应届毕业生",
                            "workCities": "深圳",
                        }
                    ]
                },
            }

    jobs = asyncio.run(
        OfficialListSource(specs=["tencent-campus"], transport=OneJobTransport()).discover(limit=5)
    )

    assert jobs and jobs[0].tier == "大厂"
