from streamlit.testing.v1 import AppTest


def test_streamlit_online_without_key_shows_error(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    app = AppTest.from_file("streamlit_app.py").run()
    app.radio[0].set_value("online").run()
    app.button[0].click().run(timeout=30)

    assert len(app.error) == 1
    assert "TAVILY_API_KEY" in app.error[0].value


def test_streamlit_demo_search_renders_intent_metrics_and_jobs():
    app = AppTest.from_file("streamlit_app.py").run()
    app.button[0].click().run(timeout=30)

    assert not app.error
    assert len(app.json) == 1
    assert len(app.metric) == 4
    assert len(app.subheader) >= 1


def test_streamlit_source_contains_failure_category_summary():
    text = open("streamlit_app.py", encoding="utf-8").read()
    assert "Counter(failure.code" in text
    assert "失败分类" in text
