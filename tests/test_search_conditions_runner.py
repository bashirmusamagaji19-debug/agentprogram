import json

import pytest

from tests.fixtures.link_receipts import VerifiedLinkStub
from web_task_agent.job_sources import AggregatorRepoSource, DiscoveredJob
from web_task_agent.models import BrowserPage
from web_task_agent.official_list_source import _SPECS, OfficialListSource
from web_task_agent.streamlit_runner import UiRunRequest, run_ui_request


@pytest.mark.asyncio
async def test_feishu_native_keyword_and_bounded_broad_fallback(monkeypatch):
    from web_task_agent.official_list_families import make_feishu_hire_lister

    calls = []
    class Transport:
        def open_json(self, method, url, *, body, headers=None):
            calls.append(dict(body))
            posts = [] if body["keyword"] or body["offset"] else [{
                "id": "1", "title": "AI Agent 实习生", "city_info": {"name": "北京"},
                "description": "开发 RAG Python AI Agent 应用。" * 10,
                "requirement": "熟悉 Python 工程。",
            }]
            return {"data": {"job_post_list": posts}}

    monkeypatch.setitem(_SPECS, "test-native", make_feishu_hire_lister(
        api_url="https://example.com/search", url_tmpl="https://example.com/jobs/{id}", company="Example"
    ))
    source = OfficialListSource(specs=["test-native"], transport=Transport(), keyword="RAG Python", location="北京")
    assert len(await source.discover(1)) == 1
    assert calls[0]["keyword"] == "RAG Python"
    assert calls[1]["keyword"] == ""
    assert len(calls) <= 3


@pytest.mark.asyncio
async def test_moka_page_keyword_and_bounded_broad_fallback(monkeypatch):
    from web_task_agent import official_list_families as families

    calls = []
    class Transport:
        def open_json(self, method, url, *, body):
            calls.append(dict(body))
            jobs = [] if body["keyword"] or body["pageNo"] > 1 else [{
                "id": "1", "title": "AI Agent 实习生", "locationNames": ["北京"],
                "jobDescription": "开发 RAG Python AI Agent 应用。" * 10,
            }]
            return {"necromancer": "fixture", "data": json.dumps({"data": {"jobs": jobs}})}

    monkeypatch.setattr(families, "decrypt_moka_envelope", lambda data, key: json.loads(data))
    monkeypatch.setitem(_SPECS, "test-moka", families.make_moka_lister(
        api_url="https://example.com/search", org_id="fixture", site_id=1, paging="page",
        url_tmpl="https://example.com/jobs/{id}", company="Example",
    ))
    source = OfficialListSource(specs=["test-moka"], transport=Transport(), keyword="RAG Python", location="北京")
    assert len(await source.discover(1)) == 1
    assert calls[0]["keyword"] == "RAG Python"
    assert calls[1]["keyword"] == ""
    assert calls[1]["pageNo"] == 1
    assert len(calls) <= 3


def discovered(index, location="北京", keyword="RAG Python"):
    return DiscoveredJob(
        url=f"https://example.com/jobs/{index}", title="AI Agent 实习生", company="Example", location=location,
        jd_text=f"岗位职责：开发 {keyword} 大模型应用。任职要求：熟悉工程与测试。" * 5,
    )


@pytest.mark.asyncio
async def test_official_conditions_filter_before_quota_and_expand_scan(monkeypatch):
    pool = [discovered(i, "深圳", "Java") for i in range(7)] + [discovered(8)]
    contexts = []
    async def lister(transport, limit):
        contexts.append((transport.search_keyword, transport.search_location, limit))
        return pool[:limit]

    monkeypatch.setitem(_SPECS, "test-search", lister)
    source = OfficialListSource(specs=["test-search"], transport=object(), keyword="RAG Python", location="北京")
    jobs = await source.discover(1)
    assert [job.url for job in jobs] == [pool[-1].url]
    assert all(item[:2] == ("RAG Python", "北京") for item in contexts)
    assert len(contexts) >= 2
    assert source.diagnostics[0]["count"] == 8
    assert source.diagnostics[0]["candidate_count"] == 1


@pytest.mark.asyncio
async def test_aggregator_filter_runs_before_limit_and_uses_city_or(tmp_path):
    pool = [discovered(1, "深圳"), discovered(2, "上海", "Java"), discovered(3, "北京、上海")]
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps({"jobs": [
        {"official_url": j.url, "title": j.title, "company_name": j.company,
         "location": j.location, "jd_text": j.jd_text} for j in pool
    ]}), encoding="utf-8")
    source = AggregatorRepoSource(str(path), keyword="RAG Python", location="北京、上海")
    assert [j.url for j in await source.discover(1)] == [pool[-1].url]


@pytest.mark.asyncio
async def test_detail_conditions_run_before_target_count_and_are_persisted(tmp_path):
    async def load(url):
        index = url.rsplit("/", 1)[-1]
        keyword = "Java" if index == "1" else "RAG Python"
        location = "深圳" if index == "2" else "上海"
        return BrowserPage(
            url=url, title="AI Agent 实习生", source="test",
            content=f"Title: AI Agent 实习生\nCompany: Example\nLocation: {location}\n"
                    f"Requirements: {keyword}\nResponsibilities: Develop AI Agent tools with {keyword}.\n" * 3,
        )

    result = await run_ui_request(
        UiRunRequest(data_mode="seed_urls", keyword="RAG Python", location="北京、上海", target_count=1,
                     seed_urls=[f"https://example.com/jobs/{i}" for i in (1, 2, 3)]),
        output_root=tmp_path, environ={}, page_loader=load, link_verifier=VerifiedLinkStub(),
    )
    assert [job.url for job in result.jobs] == ["https://example.com/jobs/3"]
    assert result.jobs[0].location == "上海"
    assert any("keyword mismatch" in row["error"] for row in result.diagnostics)
    payload = json.loads(result.artifacts["json"].read_text(encoding="utf-8"))
    assert payload["search_conditions"]["keywords"] == ["rag", "python"]


@pytest.mark.asyncio
async def test_same_runner_obeys_each_request_city_without_url_city_text(tmp_path):
    async def load(url):
        return BrowserPage(url=url, title="AI 实习生", source="test",
                           content="Title: AI 实习生\nCompany: Example\nLocation: 北京\n"
                                   "Requirements: Python\nResponsibilities: Develop AI tools.\n" * 3)

    results = []
    for city in ("北京", "上海"):
        results.append(await run_ui_request(
            UiRunRequest(data_mode="seed_urls", keyword="AI 实习", location=city,
                         seed_urls=["https://example.com/jobs/123"]),
            output_root=tmp_path, environ={}, page_loader=load, link_verifier=VerifiedLinkStub(),
        ))
    assert len(results[0].jobs) == 1
    assert results[1].jobs == []


@pytest.mark.asyncio
async def test_demo_wrong_city_cannot_consume_one_result_quota(tmp_path):
    result = await run_ui_request(
        UiRunRequest(keyword="AI intern", location="上海", target_count=1),
        output_root=tmp_path, environ={},
    )
    assert [job.title for job in result.jobs] == ["ML Platform Intern"]
    assert result.jobs[0].location == "Shanghai"


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["feishu", "moka"])
async def test_native_adapter_filters_before_quota_inside_downloaded_page(monkeypatch, platform):
    from web_task_agent import official_list_families as families

    items = [{
        "id": str(i), "title": "AI Agent 实习生", "city_info": {"name": "深圳"},
        "locationNames": ["深圳"], "description": "开发 RAG Python AI 应用。" * 15,
        "jobDescription": "开发 RAG Python AI 应用。" * 15,
    } for i in range(45)]
    items.append({**items[0], "id": "eligible", "city_info": {"name": "北京"}, "locationNames": ["北京"]})

    class Transport:
        def open_json(self, method, url, *, body, headers=None):
            batch = items if body.get("offset", 0) == 0 else []
            if platform == "feishu":
                return {"data": {"job_post_list": batch}}
            return {"necromancer": "fixture", "data": json.dumps({"data": {"jobs": batch}})}

    if platform == "feishu":
        lister = families.make_feishu_hire_lister(
            api_url="https://example.com/search", url_tmpl="https://example.com/jobs/{id}", company="Example",
        )
    else:
        monkeypatch.setattr(families, "decrypt_moka_envelope", lambda data, key: json.loads(data))
        lister = families.make_moka_lister(
            api_url="https://example.com/search", org_id="fixture", site_id=1,
            url_tmpl="https://example.com/jobs/{id}", company="Example",
        )
    monkeypatch.setitem(_SPECS, "test-page-quota", lister)
    source = OfficialListSource(specs=["test-page-quota"], transport=Transport(), keyword="RAG Python", location="北京")
    assert [job.url for job in await source.discover(1)] == ["https://example.com/jobs/eligible"]


def test_moka_location_preserves_city_in_province_and_city_structure():
    from web_task_agent.location_filter import location_matches
    from web_task_agent.official_list_families import _moka_location

    actual = _moka_location({"locations": [{"provinceName": "广东省", "cityName": "深圳市"}]})
    assert location_matches("深圳", actual)
    assert location_matches("北京", _moka_location({"locations": [{"provinceName": "北京市", "cityName": "海淀区"}]}))
