from fastapi.testclient import TestClient

from web_task_agent.open_search.api import app


def test_healthz_returns_ok():
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_run_returns_run_id_and_intent():
    with TestClient(app) as client:
        response = client.post("/api/runs", json={"query": "找北京 Agent 实习", "mode": "demo"})
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        completed = client.get(f"/api/runs/{run_id}")
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"
        jobs = client.get(f"/api/runs/{run_id}/jobs")
        assert jobs.status_code == 200
        assert jobs.json()["jobs"]
        assert response.json()["intent"]["locations"] == ["北京"]


def test_online_mode_without_key_is_structured_error(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    response = TestClient(app).post("/api/runs", json={"query": "Agent intern", "mode": "online"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "search_api_error"


def test_artifact_endpoints_reject_unknown_run():
    client = TestClient(app)
    for suffix in ("jobs", "trace", "evaluation"):
        response = client.get(f"/api/runs/missing/{suffix}")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "run_not_found"
