from urllib.error import HTTPError

import pytest

from web_task_agent.browser import PageEmptyError, PageHttpError, PageTimeoutError
from web_task_agent.models import BrowserPage
from web_task_agent.page_reliability import classify_page_content, classify_page_exception


@pytest.mark.parametrize(
    ("status", "category"),
    [(404, "removed"), (410, "removed"), (401, "blocked"), (403, "blocked"),
     (429, "blocked"), (500, "transient"), (503, "transient")],
)
def test_http_failure_categories(status, category):
    assert classify_page_exception(PageHttpError("failure", status)).category == category
    assert classify_page_exception(HTTPError("https://example.com", status, "failure", {}, None)).category == category


@pytest.mark.parametrize(
    ("error", "category"),
    [(PageTimeoutError("timeout"), "transient"), (TimeoutError(), "transient"),
     (PageEmptyError("shell"), "empty_shell"), (RuntimeError("E1005"), "unknown")],
)
def test_non_http_failures(error, category):
    assert classify_page_exception(error).category == category


@pytest.mark.parametrize(
    "text", ["该职位已下线，请查看其他职位。", "您访问的页面不存在", "This job is no longer available"]
)
def test_explicit_short_tombstones(text):
    page = BrowserPage(url="https://example.com/jobs/1", title="招聘", content=text, source="test")
    assert classify_page_content(page).category == "removed"


def test_normal_jd_mention_is_not_a_tombstone():
    page = BrowserPage(
        url="https://example.com/jobs/1", title="平台研发实习生", source="test",
        content="岗位职责：负责处理页面不存在、接口超时等异常场景。任职要求：熟悉 Python 自动化测试。" * 5,
    )
    assert classify_page_content(page) is None


def test_short_real_job_title_starting_with_error_words_is_not_removed():
    page = BrowserPage(
        url="https://example.com/jobs/1", title="页面不存在异常排查实习生", source="test",
        content="岗位职责：负责处理页面不存在、接口超时等异常场景。任职要求：熟悉 Python 自动化测试。" * 3,
    )
    assert classify_page_content(page) is None
