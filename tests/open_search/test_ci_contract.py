from pathlib import Path


def test_ci_lints_streamlit_demo_and_installs_demo_extra():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert '.[dev,demo]' in workflow
    assert "streamlit_app.py" in workflow
