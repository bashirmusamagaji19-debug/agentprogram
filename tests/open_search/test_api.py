from fastapi.testclient import TestClient

from web_task_agent.open_search.api import app


def test_healthz_returns_ok():
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_run_returns_run_id_and_intent():
    response = TestClient(app).post("/api/runs", json={"query": "找北京 Agent 实习", "mode": "demo"})
    assert response.status_code == 202
    assert response.json()["run_id"]
    assert response.json()["intent"]["locations"] == ["北京"]


def test_online_mode_without_key_is_structured_error(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    response = TestClient(app).post("/api/runs", json={"query": "Agent intern", "mode": "online"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "search_api_error"
