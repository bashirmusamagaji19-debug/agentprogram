"""Small, explicit capability probes using synthetic inputs and configured credentials."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from web_task_agent.llm_extractor import build_llm_provider_config


def _post(url, headers, payload, timeout):
    req = Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urlopen(req, timeout=timeout) as response:
        return json.load(response)


def check_deepseek_capabilities(*, transport=None) -> dict:
    config = build_llm_provider_config(provider="deepseek", model=None)
    image = base64.b64encode(Path(__file__).with_name("assets").joinpath("vision_health.png").read_bytes()).decode()
    cases = {
        "text": ("Calculate 41 * 17. Return only a JSON object with the integer field result.", {"result": 697}),
        "vision": ([
            {"type": "text", "text": (
                "Read the four-digit code and count the blue circles and red rectangles in this image. "
                "Return only JSON with fields code (string), blue_circles (integer), red_rectangles (integer)."
            )},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}},
        ], {"code": "7319", "blue_circles": 2, "red_rectangles": 1}),
    }
    result = {"provider": "deepseek", "model": config.model, "checked_at": datetime.now(UTC).isoformat()}
    call = transport or _post
    for name, (content, expected) in cases.items():
        started = monotonic()
        try:
            response = call(
                f"{config.base_url}/chat/completions",
                {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"},
                {"model": config.model, "messages": [{"role": "user", "content": content}],
                 "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"},
                 "temperature": 0, "max_tokens": 256},
                40,
            )
            actual = json.loads(response["choices"][0]["message"]["content"])
            ok = all(actual.get(key) == value for key, value in expected.items())
            entry = {"ok": ok}
            if ok:
                entry["observed"] = expected
            else:
                entry["error"] = "answer_mismatch"
            response_model = str(response.get("model", config.model))
            entry["response_model"] = response_model.replace(config.api_key, "[redacted]")[:96]
            usage = response.get("usage", {})
            entry["usage"] = {k: usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                              if isinstance(usage.get(k), int)}
        except HTTPError as exc:
            entry = {"ok": False, "http_status": exc.code, "error": "http_error"}
        except Exception as exc:
            entry = {"ok": False, "error": type(exc).__name__}
        entry["elapsed_seconds"] = round(monotonic() - started, 2)
        result[name] = entry
    return result
