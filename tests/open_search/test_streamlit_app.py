from streamlit.testing.v1 import AppTest


def test_streamlit_demo_search_renders_intent_metrics_and_jobs():
    app = AppTest.from_file("streamlit_app.py").run()
    app.button[0].click().run(timeout=30)

    assert not app.error
    assert len(app.json) == 1
    assert len(app.metric) == 4
    assert len(app.subheader) >= 1
