"""Structured data embedded in Baidu's public recruitment pages."""

from __future__ import annotations

import json
import re

BAIDU_HOST = "talent.baidu.com"
BAIDU_LIST_URL = f"https://{BAIDU_HOST}/jobs/list"
BAIDU_DETAIL_PATH = re.compile(r"/jobs/detail/(INTERN|GRADUATE|SOCIAL)/([a-zA-Z0-9-]+)/?")
BAIDU_LIST_TYPE_SOURCE = "baidu:list:recruitType"
BAIDU_DETAIL_TYPE_SOURCE = "baidu:detail:recruitType"

_SSR_SCRIPT = re.compile(
    r"<script\b[^>]*>\s*(?:window\.__USE_SSR__\s*=\s*true\s*;\s*)?"
    r"window\.__INITIAL_DATA__\s*=\s*(.*?)</script\s*>",
    re.DOTALL,
)
_JS_TOKENS = re.compile(r'"(?:[^"\\]|\\.)*"|(?P<undefined>\bundefined\b)')


def parse_baidu_page(html: str) -> dict:
    script = _SSR_SCRIPT.search(html)
    if script is None:
        raise ValueError("baidu page has no initial state")
    # The serializer emits JavaScript undefined for absent fields. Preserve
    # quoted strings verbatim, including job text that contains the same word.
    state = _JS_TOKENS.sub(lambda token: "null" if token.lastgroup else token[0], script[1])
    payload, _ = json.JSONDecoder().raw_decode(state)
    if not isinstance(payload, dict):
        raise ValueError("baidu page initial state is not an object")
    return payload


def baidu_job_text(item: dict) -> str:
    duties = str(item.get("workContent") or "").strip()
    requirements = str(item.get("serviceCondition") or "").strip()
    if not (duties or requirements):
        return ""
    location = str(item.get("workPlace") or "").strip()
    publication = str(item.get("publishDate") or "").strip()
    lines = ["公司：百度"]
    for label, value in (
        ("工作地点", location),
        ("发布日期", publication),
        ("岗位职责", "\n" + duties if duties else ""),
        ("任职要求", "\n" + requirements if requirements else ""),
    ):
        if value:
            lines.append(f"{label}：{value}")
    return "\n".join(lines)
