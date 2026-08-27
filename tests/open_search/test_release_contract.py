from pathlib import Path


def test_readme_uses_open_search_metric_boundaries():
    text = Path("README.md").read_text(encoding="utf-8")
    assert "官方/可信 ATS" in text or "可信招聘详情页" in text
    assert "fixture" in text.lower()
    assert "真实" in text


def test_web_results_escape_external_fields_and_use_safe_links():
    text = Path("src/web_task_agent/open_search/web/index.html").read_text(encoding="utf-8")
    assert "function escapeHtml" in text
    assert "escapeHtml(j.title)" in text
    assert 'rel="noopener noreferrer"' in text


def test_web_results_render_escaped_field_evidence():
    text = Path("src/web_task_agent/open_search/web/index.html").read_text(encoding="utf-8")

    assert "j.evidence" in text
    assert "escapeHtml(e.value)" in text
    assert "escapeHtml((e.snippet" in text


def test_web_page_probes_capabilities_before_online_search():
    text = Path("src/web_task_agent/open_search/web/index.html").read_text(encoding="utf-8")
    assert "loadCapabilities" in text
    assert "/api/capabilities" in text
    assert "online.disabled=true" in text


def test_readiness_probe_uses_unique_probe_file():
    text = Path("src/web_task_agent/open_search/api.py").read_text(encoding="utf-8")
    assert '.readyz-probe-{uuid4().hex}' in text
    assert "probe.unlink(missing_ok=True)" in text
