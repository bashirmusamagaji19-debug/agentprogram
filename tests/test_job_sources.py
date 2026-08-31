"""AggregatorRepoSource 测试：fixture 脱敏样本 → 解析/过滤/去重/limit 断言。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from web_task_agent.job_sources import (
    AggregatorRepoSource,
    DiscoveredJob,
    build_discovered_page,
    is_intern_ai_job,
)


@pytest.fixture
def aggregator_payload() -> dict:
    """脱敏的 job-radar jobs.json 样本：覆盖实习/AI、实习/非AI、非实习、缺失URL、重复URL。"""
    return {
        "schema_version": 1,
        "updated_at": "2026-08-31T01:06:03+00:00",
        "jobs": [
            {
                "job_id": "cn-meituan-campus:111",
                "company_name": "美团",
                "title": "大模型算法工程师（实习生）",
                "location": "北京",
                "publish_time": "2026-08-20",
                "official_url": "https://zhaopin.meituan.com/jobdetail?jobUnionId=111",
                "jd_text": "岗位职责：参与大模型研发；任职要求：熟悉 Python。",
                "tags": ["人才专项"],
            },
            {
                "job_id": "cn-tencent-campus:222",
                "company_name": "腾讯",
                "title": "混元多模态-大模型数据挖掘研究（实习生 青云计划）",
                "location": "深圳",
                "publish_time": "2026-08-25",
                "official_url": "https://careers.tencent.com/jobdesc.html?postId=222",
                "jd_text": "岗位职责：大模型数据挖掘与合成。",
                "tags": [],
            },
            {
                "job_id": "cn-tencent-campus:333",
                "company_name": "腾讯",
                "title": "前端开发（实习生）",  # 实习但非 AI → 过滤
                "location": "深圳",
                "publish_time": "2026-08-25",
                "official_url": "https://careers.tencent.com/jobdesc.html?postId=333",
                "jd_text": "",
                "tags": [],
            },
            {
                "job_id": "cn-bytedance:444",
                "company_name": "字节跳动",
                "title": "大模型算法工程师",  # AI 但非实习 → 过滤
                "location": "北京",
                "publish_time": "2026-08-25",
                "official_url": "https://jobs.bytedance.com/position/444/detail",
                "jd_text": "",
                "tags": [],
            },
            {
                "job_id": "cn-horizon:555",
                "company_name": "地平线",
                "title": "感知算法实习生",  # 无 URL → 过滤
                "location": "上海",
                "publish_time": "2026-08-25",
                "official_url": "",
                "jd_text": "",
                "tags": [],
            },
            {
                "job_id": "cn-meituan-campus:111-dup",
                "company_name": "美团",
                "title": "大模型算法工程师（实习生）",  # 与第 1 条同 URL → 去重
                "location": "北京",
                "publish_time": "2026-08-20",
                "official_url": "https://zhaopin.meituan.com/jobdetail?jobUnionId=111",
                "jd_text": "",
                "tags": [],
            },
        ],
    }


@pytest.fixture
def payload_path(tmp_path: Path, aggregator_payload: dict) -> Path:
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(aggregator_payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_discover_filters_intern_ai_and_dedupes(payload_path: Path):
    source = AggregatorRepoSource(str(payload_path))

    jobs = await source.discover(limit=10)

    urls = [job.url for job in jobs]
    assert urls == [
        "https://zhaopin.meituan.com/jobdetail?jobUnionId=111",
        "https://careers.tencent.com/jobdesc.html?postId=222",
    ]
    assert jobs[0].company == "美团"
    assert jobs[0].title == "大模型算法工程师（实习生）"
    assert jobs[0].jd_text.startswith("岗位职责")
    assert jobs[0].source == "job-radar"
    assert jobs[1].tags == []


@pytest.mark.asyncio
async def test_discover_respects_limit(payload_path: Path):
    source = AggregatorRepoSource(str(payload_path))

    jobs = await source.discover(limit=1)

    assert len(jobs) == 1
    assert jobs[0].url.endswith("jobUnionId=111")


@pytest.mark.asyncio
async def test_discover_zero_limit_returns_empty(payload_path: Path):
    source = AggregatorRepoSource(str(payload_path))

    assert await source.discover(limit=0) == []


@pytest.mark.asyncio
async def test_discover_handles_invalid_payload(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")  # 无 jobs 键

    source = AggregatorRepoSource(str(path))

    assert await source.discover(limit=10) == []


def test_is_intern_ai_job_keyword_matrix():
    assert is_intern_ai_job("大模型算法实习生")
    assert is_intern_ai_job("AI Agent 实习工程师")
    assert is_intern_ai_job("混元多模态-大模型挖掘（实习生）")
    assert not is_intern_ai_job("前端开发实习生")  # 实习但非 AI
    assert not is_intern_ai_job("大模型算法工程师")  # AI 但非实习
    assert not is_intern_ai_job("")


def test_build_discovered_page_uses_jd_text_as_content():
    job = DiscoveredJob(
        url="https://example.com/job/1",
        title="算法实习生",
        company="示例公司",
        jd_text="岗位职责：测试。",
        source="job-radar",
    )

    page = build_discovered_page(job)

    assert page.url == job.url
    assert page.title == "算法实习生"
    assert page.content == "岗位职责：测试。"
    assert page.source == "aggregator:job-radar"
    assert page.metadata["discovered_company"] == "示例公司"
