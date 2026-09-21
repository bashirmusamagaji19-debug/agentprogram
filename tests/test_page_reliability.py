from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from web_task_agent.browser import (
    MIN_USEFUL_CONTENT_CHARS,
    PageEmptyError,
    PageHttpError,
    PageTimeoutError,
)
from web_task_agent.models import BrowserPage
from web_task_agent.page_reliability import (
    PageFailure,
    PageFailureCategory,
    PageRemovedError,
    classify_page_content,
    classify_page_exception,
    safe_diagnostic_detail,
)


def test_page_failure_categories_have_stable_wire_values() -> None:
    assert [category.value for category in PageFailureCategory] == [
        "removed",
        "blocked",
        "transient",
        "empty_shell",
        "unknown",
    ]


def test_page_failure_is_frozen_and_only_removed_is_permanent() -> None:
    removed = PageFailure(PageFailureCategory.REMOVED, "gone")
    blocked = PageFailure(PageFailureCategory.BLOCKED, "forbidden")

    assert removed.is_permanent is True
    assert blocked.is_permanent is False
    with pytest.raises(FrozenInstanceError):
        removed.detail = "changed"  # type: ignore[misc]


def test_page_removed_error_is_a_runtime_error() -> None:
    assert issubclass(PageRemovedError, RuntimeError)


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (404, PageFailureCategory.REMOVED),
        (410, PageFailureCategory.REMOVED),
        (401, PageFailureCategory.BLOCKED),
        (403, PageFailureCategory.BLOCKED),
        (429, PageFailureCategory.BLOCKED),
        (503, PageFailureCategory.TRANSIENT),
    ],
)
def test_http_statuses_have_stable_failure_categories(
    status_code: int,
    expected: PageFailureCategory,
) -> None:
    assert classify_page_exception(PageHttpError("HTTP error", status_code)).category is expected


def test_timeout_is_transient() -> None:
    assert classify_page_exception(PageTimeoutError("timeout")).category is PageFailureCategory.TRANSIENT


def test_empty_page_is_empty_shell() -> None:
    assert classify_page_exception(PageEmptyError("empty")).category is PageFailureCategory.EMPTY_SHELL


def test_unknown_exception_is_unknown() -> None:
    assert classify_page_exception(RuntimeError("boom")).category is PageFailureCategory.UNKNOWN


@pytest.mark.parametrize(
    "message",
    [
        "official API returned E1005",
        "official API says Post Unavailable",
    ],
)
def test_business_unavailable_signals_are_removed(message: str) -> None:
    assert classify_page_exception(RuntimeError(message)).category is PageFailureCategory.REMOVED


def test_safe_diagnostic_detail_collapses_redacts_and_truncates() -> None:
    detail = safe_diagnostic_detail(
        "Authorization=Bearer auth-secret\n"
        "api_key=api-secret access_token=access-secret token=token-secret "
        + "x" * 300
    )

    assert "auth-secret" not in detail
    assert "api-secret" not in detail
    assert "access-secret" not in detail
    assert "token-secret" not in detail
    assert "\n" not in detail
    assert len(detail) <= 160
    assert len(safe_diagnostic_detail("x" * 300, max_chars=20)) == 20


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("{'Authorization': 'Bearer top-secret'}", "top-secret"),
        ('{"Authorization": "Bearer top-secret"}', "top-secret"),
        ("{'api_key': 'api-secret'}", "api-secret"),
        ('{"access_token": "access-secret"}', "access-secret"),
        ("{'token': 'token-secret'}", "token-secret"),
    ],
)
def test_safe_diagnostic_detail_redacts_quoted_credential_keys(
    raw: str,
    secret: str,
) -> None:
    assert secret not in safe_diagnostic_detail(raw)


@pytest.mark.parametrize(
    "marker",
    [
        "职位已下线",
        "岗位已下线",
        "职位不存在",
        "岗位不存在",
        "页面不存在",
        "job is no longer available",
        "position is no longer available",
    ],
)
def test_short_page_with_explicit_tombstone_is_removed(marker: str) -> None:
    page = BrowserPage(
        url="https://example.com/jobs/removed",
        title=marker,
        content="该岗位无法继续申请。",
    )

    failure = classify_page_content(page)

    assert failure is not None
    assert failure.category is PageFailureCategory.REMOVED


def test_long_page_with_tombstone_in_title_is_removed() -> None:
    content = "导航栏目无关内容。" * 20
    page = BrowserPage(
        url="https://example.com/jobs/removed",
        title="职位已下线",
        content=content,
    )

    assert len(content) >= MIN_USEFUL_CONTENT_CHARS
    failure = classify_page_content(page)

    assert failure is not None
    assert failure.category is PageFailureCategory.REMOVED


def test_long_normal_job_description_mentioning_tombstone_phrase_is_live() -> None:
    content = (
        "岗位职责：负责处理页面不存在、接口超时等异常场景并完善监控。"
        "任职要求：熟悉 Python、HTTP、自动化测试和服务可靠性工程。"
    ) * 4
    page = BrowserPage(
        url="https://example.com/jobs/live",
        title="平台研发实习生",
        content=content,
    )

    assert len(content) >= MIN_USEFUL_CONTENT_CHARS
    assert classify_page_content(page) is None


def test_short_non_tombstone_page_is_empty_shell() -> None:
    page = BrowserPage(
        url="https://example.com/jobs/shell",
        title="招聘",
        content="请启用 JavaScript 后继续访问。",
    )

    failure = classify_page_content(page)

    assert failure is not None
    assert failure.category is PageFailureCategory.EMPTY_SHELL


def test_long_page_is_live() -> None:
    page = BrowserPage(
        url="https://example.com/jobs/live",
        title="大模型实习生",
        content="岗位职责和任职要求。" * 30,
    )

    assert classify_page_content(page) is None
