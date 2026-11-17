import json
from io import BytesIO
from urllib.error import HTTPError

from web_task_agent.provider_health import check_deepseek_capabilities


def test_capability_probe_checks_real_image_payload_and_expected_answers(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    calls = []

    def transport(url, headers, payload, timeout):
        calls.append(payload)
        content = payload["messages"][0]["content"]
        if isinstance(content, list):
            assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
            assert "7319" not in content[0]["text"]
            answer = {"code": "7319", "blue_circles": 2, "red_rectangles": 1}
        else:
            answer = {"result": 697}
        return {"model": payload["model"], "choices": [{"message": {"content": json.dumps(answer)}}]}

    result = check_deepseek_capabilities(transport=transport)

    assert result["text"]["ok"] is True
    assert result["vision"]["ok"] is True
    assert len(calls) == 2
    assert "test-key" not in json.dumps(result)


def test_successful_http_with_wrong_visual_answer_is_not_reported_as_capable(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    def transport(*args):
        return {"choices": [{"message": {"content": '{"result":697}'}}]}

    result = check_deepseek_capabilities(transport=transport)
    assert result["text"]["ok"] is True
    assert result["vision"]["ok"] is False
    assert result["vision"]["error"] == "answer_mismatch"


def test_authentication_failure_does_not_expose_secret_or_error_body(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-value")

    def transport(*args):
        raise HTTPError("https://api.deepseek.com/chat/completions", 401, "secret-value", {},
                        BytesIO(b'{"error":"secret-value"}'))

    result = check_deepseek_capabilities(transport=transport)
    assert result["text"]["http_status"] == 401
    assert result["vision"]["ok"] is False
    assert "secret-value" not in json.dumps(result)
