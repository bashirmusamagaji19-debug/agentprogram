from fastapi.testclient import TestClient

from web_task_agent.open_search import api
from web_task_agent.open_search.api import app


def test_healthz_returns_ok():
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_capabilities_do_not_expose_secrets(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "secret-value")
    response = TestClient(app).get("/api/capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["modes"]["demo"]["available"] is True
    assert payload["modes"]["online"]["available"] is True
    assert "secret-value" not in response.text


def test_version_reports_non_sensitive_runtime_metadata():
    response = TestClient(app).get("/api/version")
    assert response.status_code == 200
    payload = response.json()
    assert payload["project"] == "web-task-agent"
    assert payload["version"] == "0.1.0"
    assert "python" in payload
    assert "limits" in payload


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
        evaluation = client.get(f"/api/runs/{run_id}/evaluation")
        assert evaluation.json()["evaluation"]["available"] is True
        assert evaluation.json()["evaluation"]["jobs_count"] >= 1
        assert evaluation.json()["evaluation"]["failure_counts"] == {}
        assert response.json()["intent"]["locations"] == ["北京"]


def test_online_mode_without_key_is_structured_error(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    response = TestClient(app).post("/api/runs", json={"query": "Agent intern", "mode": "online"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "search_api_error"


def test_query_length_is_bounded():
    response = TestClient(app).post("/api/runs", json={"query": "x" * 501, "mode": "demo"})
    assert response.status_code == 422


def test_run_registry_is_bounded(monkeypatch):
    monkeypatch.setattr(api, "_MAX_RUNS", 1)
    api._runs.clear()
    with TestClient(app) as client:
        first = client.post("/api/runs", json={"query": "Agent", "mode": "demo"}).json()["run_id"]
        second = client.post("/api/runs", json={"query": "Python", "mode": "demo"}).json()["run_id"]
    assert first != second
    assert first not in api._runs
    assert second in api._runs


def test_create_run_is_rate_limited(monkeypatch):
    monkeypatch.setattr(api, "_MAX_REQUESTS_PER_MINUTE", 1)
    api._request_windows.clear()
    with TestClient(app) as client:
        first = client.post("/api/runs", json={"query": "Agent", "mode": "demo"})
        second = client.post("/api/runs", json={"query": "Python", "mode": "demo"})
    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "rate_limited"


def test_runtime_limits_have_documented_defaults():
    assert api._MAX_RUNS >= 1
    assert api._MAX_REQUESTS_PER_MINUTE >= 1


def test_artifact_endpoints_reject_unknown_run():
    client = TestClient(app)
    response = client.get("/api/runs/missing")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "run_not_found"
    for suffix in ("jobs", "trace", "evaluation"):
        response = client.get(f"/api/runs/missing/{suffix}")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "run_not_found"
