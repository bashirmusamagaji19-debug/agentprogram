from pathlib import Path

from streamlit.testing.v1 import AppTest

from web_task_agent.streamlit_app import build_configured_persistence
from web_task_agent.streamlit_runner import load_persisted_ui_result


def test_browser_ui_can_pause_and_approve_without_runtime_in_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"
    app = AppTest.from_file(str(app_path), default_timeout=20).run()
    assert not app.exception
    next(
        control for control in app.segmented_control if control.label == "数据模式"
    ).set_value("内置 Demo")
    app.run()
    assert not app.exception
    for control in app.text_input:
        if control.label == "岗位关键词":
            control.set_value("AI intern")
        if control.label == "地点":
            control.set_value("全国")
    for control in app.number_input:
        if control.label == "目标岗位数":
            control.set_value(1)
    for control in app.toggle:
        if control.label == "保存前确认":
            control.set_value(True)
    next(button for button in app.button if button.label == "开始运行岗位 Agent").click().run()
    assert not app.exception
    assert "latest_ui_result" not in app.session_state
    run_id = app.session_state["latest_run_id"]
    stores = build_configured_persistence("streamlit-persistence")
    pending = load_persisted_ui_result(*stores, "demo", run_id)
    assert pending.run_status == "awaiting_approval"
    assert pending.engine == "hybrid"
    assert any(pending.approval["approval_id"] in item.value for item in app.caption)
    assert any(pending.approval["payload_hash"] in item.value for item in app.caption)
    state_values = getattr(app.session_state, "filtered_state", app.session_state)
    assert all("runtime" not in str(key) for key in state_values)
    next(button for button in app.button if button.label == "确认保存").click().run()
    assert not app.exception
    assert app.session_state["latest_run_id"] == run_id
    assert "latest_ui_result" not in app.session_state
    assert load_persisted_ui_result(*stores, "demo", run_id).run_status == "succeeded"
    assert not [button for button in app.button if button.label == "确认保存"]
