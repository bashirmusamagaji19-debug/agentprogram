from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

UiDataMode = Literal["demo", "aggregator", "seed_urls"]

PROVIDER_API_KEY_ENV = {
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
}


class UiRequestError(ValueError):
    """A safe validation error suitable for display in the Web UI."""


class UiRunRequest(BaseModel):
    keyword: str = "AI Agent 实习"
    location: str = "全国"
    target_count: int = Field(default=10, ge=1, le=50)
    skills: list[str] = Field(default_factory=list)
    resume_text: str = ""
    data_mode: UiDataMode = "demo"
    aggregator_path: str | None = None
    seed_urls: list[str] = Field(default_factory=list)
    llm_extractor_provider: Literal["deepseek", "qwen"] | None = None
    llm_match_provider: Literal["deepseek", "qwen"] | None = None


class UiRunResult(BaseModel):
    run_id: str
    artifacts: dict[str, Path] = Field(default_factory=dict)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = value.strip()
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def parse_skills(value: str) -> list[str]:
    return _unique(re.split(r"[,，\n;；]+", value))


def parse_seed_urls(value: str) -> list[str]:
    return _unique(re.split(r"[\s,，]+", value))


def decode_resume_upload(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise UiRequestError("简历文件必须使用 UTF-8 编码。") from exc


def validate_ui_request(
    request: UiRunRequest,
    *,
    environ: Mapping[str, str],
) -> None:
    if request.data_mode == "aggregator" and not request.aggregator_path:
        raise UiRequestError("请上传聚合岗位 JSON 文件。")
    if request.data_mode == "seed_urls":
        if not request.seed_urls:
            raise UiRequestError("请至少填写一个 HTTP(S) 岗位 URL。")
        invalid = [url for url in request.seed_urls if not _is_http_url(url)]
        if invalid:
            raise UiRequestError("指定岗位 URL 必须是有效的 HTTP(S) 地址。")

    providers = {
        provider
        for provider in (
            request.llm_extractor_provider,
            request.llm_match_provider,
        )
        if provider
    }
    missing = sorted(
        PROVIDER_API_KEY_ENV[provider]
        for provider in providers
        if not environ.get(PROVIDER_API_KEY_ENV[provider], "").strip()
    )
    if missing:
        raise UiRequestError(f"缺少模型环境变量：{', '.join(missing)}")


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
