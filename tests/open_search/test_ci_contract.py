from pathlib import Path


def test_ci_lints_streamlit_demo_and_installs_demo_extra():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert '.[dev,demo]' in workflow
    assert "streamlit_app.py" in workflow


def test_ci_builds_docker_image_without_search_api_secrets():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    docker_job = workflow.split("  docker-build:", maxsplit=1)[1]

    assert "docker build --tag web-task-agent:ci ." in docker_job
    assert "TAVILY_API_KEY" not in docker_job
    assert "DASHSCOPE_API_KEY" not in docker_job
