from __future__ import annotations

import socket
from dataclasses import FrozenInstanceError
from urllib.error import HTTPError, URLError

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


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("timeout"),
        ConnectionError("connection failed"),
        ConnectionResetError("connection reset"),
        socket.gaierror("name resolution failed"),
        URLError("network unavailable"),
    ],
)
def test_raw_network_errors_are_transient(exc: Exception) -> None:
    assert classify_page_exception(exc).category is PageFailureCategory.TRANSIENT


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (404, PageFailureCategory.REMOVED),
        (410, PageFailureCategory.REMOVED),
        (401, PageFailureCategory.BLOCKED),
        (403, PageFailureCategory.BLOCKED),
        (429, PageFailureCategory.BLOCKED),
        (500, PageFailureCategory.TRANSIENT),
        (503, PageFailureCategory.TRANSIENT),
    ],
)
def test_raw_http_errors_are_classified_by_status(
    status_code: int,
    expected: PageFailureCategory,
) -> None:
    exc = HTTPError(
        "https://example.com/jobs/1",
        status_code,
        "request failed",
        None,
        None,
    )

    assert classify_page_exception(exc).category is expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("E1005", PageFailureCategory.REMOVED),
        ("post unavailable", PageFailureCategory.REMOVED),
        ("bad request", PageFailureCategory.UNKNOWN),
    ],
)
def test_unmapped_raw_http_errors_still_check_business_markers(
    message: str,
    expected: PageFailureCategory,
) -> None:
    exc = HTTPError(
        "https://example.com/jobs/1",
        400,
        message,
        None,
        None,
    )

    assert classify_page_exception(exc).category is expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("E1005", PageFailureCategory.REMOVED),
        ("server error", PageFailureCategory.TRANSIENT),
    ],
)
def test_raw_http_500_business_marker_takes_priority(
    message: str,
    expected: PageFailureCategory,
) -> None:
    exc = HTTPError(
        "https://example.com/jobs/1",
        500,
        message,
        None,
        None,
    )

    assert classify_page_exception(exc).category is expected


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
    "key",
    [
        "id_token",
        "auth_token",
        "session_token",
        "refresh_token",
        "access_token",
        "token",
        "api_key",
        "authorization",
        "password",
        "client_secret",
        "secret",
    ],
)
def test_safe_diagnostic_detail_redacts_credential_key_values(
    key: str,
) -> None:
    assert "top-secret" not in safe_diagnostic_detail(f"{key}=top-secret")


@pytest.mark.parametrize(
    "key",
    [
        "id_token",
        "auth_token",
        "session_token",
        "refresh_token",
        "access_token",
        "token",
        "api_key",
        "authorization",
        "password",
        "client_secret",
        "secret",
    ],
)
def test_safe_diagnostic_detail_redacts_quoted_credential_keys(
    key: str,
) -> None:
    assert "top-secret" not in safe_diagnostic_detail(f'{{"{key}": "top-secret"}}')


def test_safe_diagnostic_detail_redacts_bare_bearer_token() -> None:
    detail = safe_diagnostic_detail("request failed with Bearer sk-live-secret")

    assert "sk-live-secret" not in detail
    assert "Bearer" in detail


def test_safe_diagnostic_detail_redacts_bare_basic_credential() -> None:
    detail = safe_diagnostic_detail("request failed with Basic dXNlcjpwYXNz")

    assert "dXNlcjpwYXNz" not in detail
    assert "Basic" in detail


def test_safe_diagnostic_detail_redacts_signed_url_parameter() -> None:
    detail = safe_diagnostic_detail(
        "https://example.com/jobs?X-Amz-Signature=signature-secret"
    )

    assert "signature-secret" not in detail
    assert "X-Amz-Signature" in detail


def test_safe_diagnostic_detail_redacts_sig_query_parameter() -> None:
    detail = safe_diagnostic_detail(
        "https://example.com/jobs?sig=signature-secret"
    )

    assert "signature-secret" not in detail
    assert "sig" in detail


def test_safe_diagnostic_detail_redacts_url_password_only() -> None:
    detail = safe_diagnostic_detail(
        "fetch https://user:url-secret@example.com/jobs"
    )

    assert detail == "fetch https://user:<redacted>@example.com/jobs"


def test_safe_diagnostic_detail_redacts_token_only_url_userinfo() -> None:
    detail = safe_diagnostic_detail(
        "fetch https://token-secret@example.com/jobs"
    )

    assert detail == "fetch https://<redacted>@example.com/jobs"


def test_safe_diagnostic_detail_redacts_opaque_url_userinfo() -> None:
    detail = safe_diagnostic_detail(
        "fetch https://Ab9xZ7Q2KpLmNoPq4Rt6@example.com/jobs"
    )

    assert detail == "fetch https://<redacted>@example.com/jobs"


def test_safe_diagnostic_detail_redacts_cookie_header() -> None:
    detail = safe_diagnostic_detail("Cookie: session=cookie-secret; Path=/")

    assert "cookie-secret" not in detail
    assert "Cookie:" in detail


@pytest.mark.parametrize(
    "key",
    ["Cookie", "Set-Cookie"],
)
def test_safe_diagnostic_detail_redacts_quoted_cookie_keys(key: str) -> None:
    detail = safe_diagnostic_detail(
        f'{{"{key}": "session=cookie-secret; Path=/"}}'
    )

    assert "cookie-secret" not in detail


def test_safe_diagnostic_detail_does_not_redact_plain_cookie_text() -> None:
    raw = "cookie handling failed"

    assert safe_diagnostic_detail(raw) == raw


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Users\Alice Smith\resume.pdf",
        "C:/Users/Alice Smith/resume.pdf",
        "C:\\Program Files\\Acme\\",
        r"D:\data\resume.pdf",
        r"C:\Program Files\Acme\config.json",
        r"D:\data\Alice Smith\session",
        "D:\\data\\Alice Smith\\session\\",
        r"\\server\share\private\resume.pdf",
        "\\\\server\\share\\private\\",
        "C:\\",
        "\\\\server\\share\\",
        "/home/alice/private/resume.pdf",
        "/Users/alice/private/resume.pdf",
        "/var/private/resume.pdf",
        "/var/private/session",
        "/tmp/private/resume.pdf",
        "/root/private/resume.pdf",
    ],
)
def test_safe_diagnostic_detail_replaces_local_paths(path: str) -> None:
    detail = safe_diagnostic_detail(f"failed to read {path}")

    assert path not in detail
    assert "alice" not in detail.casefold()
    assert "resume.pdf" not in detail.casefold()
    assert "private" not in detail.casefold()
    assert "acme" not in detail.casefold()
    assert "server" not in detail.casefold()
    assert "session" not in detail.casefold()
    assert "\\" not in detail
    assert "/" not in detail
    assert "<local-path>" in detail


def test_safe_diagnostic_detail_does_not_redact_regular_https_path() -> None:
    raw = "see https://example.com/home/user for details"

    assert safe_diagnostic_detail(raw) == raw


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
