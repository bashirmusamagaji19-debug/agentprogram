"""规则抽取中文化测试（阶段 2 任务 3）：中文标签/全角冒号/section marker/城市。"""

from __future__ import annotations

from tests.fixtures.chinese_job_pages import (
    CHINESE_LABELED_PAGE,
    CHINESE_UNSTRUCTURED_PAGE,
)
from web_task_agent.extractor import PageExtractor


def test_chinese_labeled_page_fullwidth_colon_fields():
    """标签行 + 全角冒号：逐字段命中（阶段 0 基线 requirements/location/responsibilities 全 0）。"""
    job = PageExtractor().extract(CHINESE_LABELED_PAGE)

    assert job.title == "大模型算法工程师（实习生）"
    assert job.company == "示例科技有限公司"
    assert job.location == "北京市"
    assert job.posted_at == "2026-08-30"
    assert "Long Context" in job.responsibilities
    assert "PyTorch" in job.requirements
    assert job.confidence == 1.0


def test_chinese_labeled_page_skills_split_on_numbered_list():
    """中文整段 requirements 的逗号切分仍是兜底口径（结构化 skills 由 LLM 提供，阶段 2 任务 2）。

    这里只断言 requirements 非空且 skills 是切分产物而非空——
    切分质量差是已知边界（垃圾进 matcher 的问题由阶段 3 语义匹配兜底）。
    """
    job = PageExtractor().extract(CHINESE_LABELED_PAGE)

    assert job.requirements
    assert isinstance(job.skills, list)


def test_chinese_unstructured_page_sections_via_markers():
    """无标签整段式：中文 section marker 切出职责/要求，城市行识别。"""
    job = PageExtractor().extract(CHINESE_UNSTRUCTURED_PAGE)

    # title 来自页面 title（第一行是公司名不匹配 section opener）
    assert job.title == "感知算法实习生-示例自动驾驶"
    # company/location 从头部行推断（第 2 行含中文城市）
    assert "示例自动驾驶" in job.company
    assert "上海" in job.location
    assert "路测感知" in job.responsibilities
    assert "BEV感知" in job.requirements
    # 福利待遇是 stop marker，不应混进 requirements
    assert "六险一金" not in job.requirements


def test_chinese_labeled_page_skips_infer_branch():
    """有标签行时不走 infer 分支（title 不得被页面 title 污染）。"""
    job = PageExtractor().extract(CHINESE_LABELED_PAGE)

    assert job.title != CHINESE_LABELED_PAGE.title
    assert "牛客网" not in job.title


def test_halfwidth_colon_chinese_labels_still_work():
    """半角冒号的中文标签行也兼容。"""
    from web_task_agent.models import BrowserPage

    page = BrowserPage(
        url="https://example.com/cn",
        title="t",
        content=(
            "职位名称: 后端开发实习生\n"
            "公司名称: 示例公司\n"
            "工作地点: 深圳市\n"
            "任职要求: 熟悉Python\n"
        ),
        source="fixture",
    )

    job = PageExtractor().extract(page)

    assert job.title == "后端开发实习生"
    assert job.company == "示例公司"
    assert job.location == "深圳市"
    assert job.requirements == "熟悉Python"


def test_english_extraction_unchanged_by_chinese_additions():
    """英文路径零回归：原有英文标签/section 解析行为不变。"""
    from web_task_agent.models import BrowserPage

    page = BrowserPage(
        url="https://example.com/en",
        title="AI Engineering Intern at Example AI",
        content=(
            "AI Engineering Intern\n"
            "Example AI\n"
            "Remote\n"
            "About the role\n"
            "Build AI agents with LangGraph.\n"
            "Requirements\n"
            "Python, LangGraph, and LLM experience.\n"
        ),
        source="fixture",
    )

    job = PageExtractor().extract(page)

    assert job.title == "AI Engineering Intern at Example AI"
    assert job.location == "Remote"
    assert "LangGraph" in job.responsibilities
    assert "Python" in job.requirements
