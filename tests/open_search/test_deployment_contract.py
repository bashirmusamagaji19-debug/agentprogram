from pathlib import Path


def test_docker_deployment_contract_is_present():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "uvicorn web_task_agent.open_search.api:app" in dockerfile
    assert "${PORT:-8000}" in dockerfile
    ignored = Path(".dockerignore").read_text(encoding="utf-8")
    assert ".env" in ignored
