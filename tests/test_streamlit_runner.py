from __future__ import annotations

import pytest

from web_task_agent.streamlit_runner import (
    UiRequestError,
    UiRunRequest,
    decode_resume_upload,
    parse_seed_urls,
    parse_skills,
    validate_ui_request,
)


def test_parse_skills_normalizes_commas_lines_and_duplicates() -> None:
    assert parse_skills("Python，RAG\npython, LangGraph") == [
        "Python",
        "RAG",
        "LangGraph",
    ]


def test_parse_seed_urls_keeps_unique_http_urls() -> None:
    assert parse_seed_urls(
        "https://example.com/jobs/1\nhttps://example.com/jobs/1\nhttp://example.com/jobs/2"
    ) == ["https://example.com/jobs/1", "http://example.com/jobs/2"]


def test_decode_resume_upload_accepts_utf8_text() -> None:
    assert decode_resume_upload("项目：LangGraph Agent".encode()) == "项目：LangGraph Agent"


def test_decode_resume_upload_rejects_invalid_utf8() -> None:
    with pytest.raises(UiRequestError, match="UTF-8"):
        decode_resume_upload(b"\xff\xfe")


def test_seed_url_mode_requires_http_urls() -> None:
    request = UiRunRequest(data_mode="seed_urls", seed_urls=["not-a-url"])

    with pytest.raises(UiRequestError, match="HTTP"):
        validate_ui_request(request, environ={})


def test_aggregator_mode_requires_uploaded_json_path() -> None:
    request = UiRunRequest(data_mode="aggregator")

    with pytest.raises(UiRequestError, match="聚合岗位 JSON"):
        validate_ui_request(request, environ={})


def test_provider_validation_names_missing_variable_without_value() -> None:
    request = UiRunRequest(llm_extractor_provider="qwen")

    with pytest.raises(UiRequestError, match="DASHSCOPE_API_KEY") as captured:
        validate_ui_request(request, environ={})

    assert "secret-value" not in str(captured.value)


def test_provider_validation_accepts_configured_environment() -> None:
    request = UiRunRequest(
        llm_extractor_provider="deepseek",
        llm_match_provider="qwen",
    )

    validate_ui_request(
        request,
        environ={"DEEPSEEK_API_KEY": "secret-value", "DASHSCOPE_API_KEY": "secret-value"},
    )


def test_request_rejects_target_count_above_ui_limit() -> None:
    with pytest.raises(ValueError):
        UiRunRequest(target_count=51)
