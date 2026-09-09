"""页面可打开性甄别:正文来源透传 + 详情接口懒探测。"""

from __future__ import annotations

from web_task_agent.extractor import PageExtractor
from web_task_agent.models import BrowserPage


def _extract(content: str, metadata: dict):
    return PageExtractor().extract(
        BrowserPage(url="https://x", title="t", content=content, source="s", metadata=metadata)
    )


def test_official_api_origin_marks_api_verified():
    """正文来自官方详情接口 → api-verified(页面渲染有据)。"""
    content = "公司:腾讯\n岗位职责:\n1、负责大模型研发与落地，覆盖训练推理全链路工程化部署。\n任职要求:\n1、硕士及以上学历。"
    job = _extract(content, {"content_origin": "official-api"})
    assert job.url_verification == "api-verified"


def test_jd_text_origin_marks_list_attested():
    """正文来自列表自带 JD → list-attested(id 有效性未独立验证)。"""
    content = "职位描述\n1、负责具身智能大模型研发，打造行业领先的具身智能基座能力与算法体系。\n任职要求\n1、计算机相关专业。"
    job = _extract(content, {"content_origin": "jd_text-fallback", "discovered_company": "星尘智能"})
    assert job.url_verification == "list-attested"


def test_http_origin_marks_http_fetched():
    content = "岗位职责:\n1、负责大模型数据管线研发与训练效率优化，支撑大规模预训练任务稳定运行。\n任职要求:\n1、本科及以上学历。"
    job = _extract(content, {"content_origin": "http"})
    assert job.url_verification == "http-fetched"


def test_missing_origin_defaults_unverified():
    content = "岗位职责:\n1、负责大模型研发与落地，覆盖训练推理全链路工程化部署与优化迭代。\n任职要求:\n1、本科及以上学历。"
    job = _extract(content, {})
    assert job.url_verification == "unverified"
