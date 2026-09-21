from __future__ import annotations

import re
import unicodedata

_SEPARATORS = re.compile(r"[,，、;/；|\s\-—]+")
_WILDCARDS = {"全国", "不限", "任意", "any", "anywhere", "all"}
_UNKNOWN = {"", "unknown", "unknown location", "未知", "未知地点", "未标注", "未注明"}
_REMOTE = {"remote", "远程"}
_SUFFIXES = ("特别行政区", "自治区", "自治州", "省", "市")


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _canonical_token(value: str) -> str:
    token = _normalize(value)
    if token in _REMOTE:
        return "remote"
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) > len(suffix):
            return token[: -len(suffix)]
    return token


def _tokens(value: str) -> list[str]:
    return [
        token
        for token in (_canonical_token(part) for part in _SEPARATORS.split(_normalize(value)))
        if token
    ]


def is_unknown_location(value: str) -> bool:
    return _normalize(value) in _UNKNOWN


def location_matches(requested_location: str, actual_location: str) -> bool:
    requested = _normalize(requested_location)
    if requested in _WILDCARDS:
        return True
    if is_unknown_location(actual_location):
        return False
    requested_tokens = _tokens(requested_location)
    actual_tokens = _tokens(actual_location)
    return bool(requested_tokens) and any(
        requested_token == actual_token or requested_token in actual_token
        for requested_token in requested_tokens
        for actual_token in actual_tokens
    )
