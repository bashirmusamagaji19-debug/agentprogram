from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from web_task_agent.artifact_store import ArtifactNotFoundError, FileArtifactStore
from web_task_agent.run_store import FileRunStore, RunRecord
from web_task_agent.streamlit_app import (
    app_access_granted,
    artifact_download_spec,
    build_configured_persistence,
    configured_app_passcode,
    recent_run_rows,
)
from web_task_agent.streamlit_runner import (
    RETENTION_DAYS,
    UiRunRequest,
    load_persisted_ui_result,
    run_ui_request,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_run_ui_request_persists_successful_run_and_artifacts(tmp_path) -> None:
    run_store = FileRunStore(tmp_path / "runs")
    artifact_store = FileArtifactStore(tmp_path / "artifacts")

    result = await run_ui_request(
        UiRunRequest(
            keyword="AI intern",
            target_count=2,
            skills=["Python", "LangGraph"],
            resume_text="PRIVATE RESUME CONTENT",
        ),
        output_root=tmp_path / "output",
        environ={},
        run_store=run_store,
        artifact_store=artifact_store,
        owner_id="owner-a",
    )

    record = run_store.get_run("owner-a", result.run_id)
    assert record is not None
    assert record.status == "succeeded"
    assert record.metrics["valid_jobs"] == 2
    assert "PRIVATE RESUME CONTENT" not in record.model_dump_json()
    assert "resume_text" not in record.request_summary

    artifacts = {
        item.artifact_key: item
        for item in artifact_store.list_artifacts("owner-a", result.run_id)
    }
    assert set(artifacts) == {"json", "report", "dashboard", "action_plan"}
    assert set(result.artifact_records) == set(artifacts)
    assert all(item.sha256 for item in artifacts.values())


@pytest.mark.asyncio
async def test_run_ui_request_persists_partial_when_page_failures_exist(tmp_path) -> None:
    run_store = FileRunStore(tmp_path / "runs")
    artifact_store = FileArtifactStore(tmp_path / "artifacts")

    result = await run_ui_request(
        UiRunRequest(
            data_mode="seed_urls",
            seed_urls=["https://example.invalid/jobs/missing"],
        ),
        output_root=tmp_path / "output",
        environ={},
        page_loader=lambda _url: (_ for _ in ()).throw(ValueError("page unavailable")),
        run_store=run_store,
        artifact_store=artifact_store,
        owner_id="owner-a",
    )

    record = run_store.get_run("owner-a", result.run_id)
    assert record is not None
    assert record.status == "partial"
    assert record.error_summary


@pytest.mark.asyncio
async def test_persisted_result_recovers_after_store_reinstantiation(tmp_path) -> None:
    output_root = tmp_path / "output"
    first_run_store = FileRunStore(tmp_path / "runs")
    first_artifact_store = FileArtifactStore(tmp_path / "artifacts")
    result = await run_ui_request(
        UiRunRequest(),
        output_root=output_root,
        environ={},
        run_store=first_run_store,
        artifact_store=first_artifact_store,
        owner_id="owner-a",
    )

    restarted_run_store = FileRunStore(tmp_path / "runs")
    restarted_artifact_store = FileArtifactStore(tmp_path / "artifacts")
    restored = load_persisted_ui_result(
        restarted_run_store,
        restarted_artifact_store,
        "owner-a",
        result.run_id,
    )

    assert restored is not None
    assert restored.run_id == result.run_id
    assert len(restored.jobs) == len(result.jobs)
    label, file_name, mime, content = artifact_download_spec(
        restored,
        "report",
        artifact_store=restarted_artifact_store,
        owner_id="owner-a",
    )
    assert label == "下载 Markdown 报告"
    assert file_name.endswith(".md")
    assert mime == "text/markdown"
    assert "AI 实习岗位搜索报告" in content.decode("utf-8")


@pytest.mark.asyncio
async def test_persisted_result_isolated_by_owner(tmp_path) -> None:
    run_store = FileRunStore(tmp_path / "runs")
    artifact_store = FileArtifactStore(tmp_path / "artifacts")
    result = await run_ui_request(
        UiRunRequest(),
        output_root=tmp_path / "output",
        environ={},
        run_store=run_store,
        artifact_store=artifact_store,
        owner_id="owner-a",
    )

    assert (
        load_persisted_ui_result(
            run_store,
            artifact_store,
            "owner-b",
            result.run_id,
        )
        is None
    )
    with pytest.raises(ArtifactNotFoundError):
        artifact_store.get_artifact("owner-b", result.run_id, "report")


@pytest.mark.asyncio
async def test_persisted_run_uses_seven_day_retention(tmp_path) -> None:
    run_store = FileRunStore(tmp_path / "runs")
    artifact_store = FileArtifactStore(tmp_path / "artifacts")

    result = await run_ui_request(
        UiRunRequest(),
        output_root=tmp_path / "output",
        environ={},
        run_store=run_store,
        artifact_store=artifact_store,
        owner_id="owner-a",
    )

    before = datetime.now(UTC)
    record = run_store.get_run("owner-a", result.run_id)
    artifact = artifact_store.list_artifacts("owner-a", result.run_id)[0]
    assert record is not None
    assert before + timedelta(days=RETENTION_DAYS) - timedelta(minutes=1) <= record.retention_deadline
    assert (
        before + timedelta(days=RETENTION_DAYS) + timedelta(minutes=1)
        >= record.retention_deadline
    )
    assert artifact.retention_deadline == record.retention_deadline


@pytest.mark.asyncio
async def test_persisted_artifacts_do_not_contain_resume_or_absolute_paths(tmp_path) -> None:
    run_store = FileRunStore(tmp_path / "runs")
    artifact_store = FileArtifactStore(tmp_path / "artifacts")
    output_root = tmp_path / "output"
    private_resume = "PRIVATE-RESUME-TOKEN-4f04"

    result = await run_ui_request(
        UiRunRequest(
            keyword="AI intern",
            target_count=2,
            skills=["Python", "LangGraph"],
            resume_text=private_resume,
        ),
        output_root=output_root,
        environ={},
        run_store=run_store,
        artifact_store=artifact_store,
        owner_id="owner-a",
    )

    persisted_files = [
        path
        for base in (tmp_path / "runs", tmp_path / "artifacts")
        for path in base.rglob("*")
        if path.is_file()
    ]
    combined = b"\n".join(path.read_bytes() for path in persisted_files)
    record = run_store.get_run("owner-a", result.run_id)
    assert private_resume.encode() not in combined
    assert str(output_root.resolve()).encode() not in combined
    assert record is not None
    assert record.run_id == result.run_id


@pytest.mark.asyncio
async def test_failed_run_is_recorded_without_leaking_exception_text(
    tmp_path,
    monkeypatch,
) -> None:
    run_store = FileRunStore(tmp_path / "runs")
    artifact_store = FileArtifactStore(tmp_path / "artifacts")

    async def fail_run(self, *, user, run_id):  # noqa: ANN001
        raise RuntimeError("secret-value-should-not-be-persisted")

    monkeypatch.setattr(
        "web_task_agent.streamlit_runner.WebTaskWorkflow.run_with_langgraph",
        fail_run,
    )

    with pytest.raises(RuntimeError, match="secret-value"):
        await run_ui_request(
            UiRunRequest(),
            output_root=tmp_path / "output",
            environ={},
            run_store=run_store,
            artifact_store=artifact_store,
            owner_id="owner-a",
        )

    records = run_store.list_runs("owner-a")
    assert len(records) == 1
    assert records[0].status == "failed"
    assert "secret-value-should-not-be-persisted" not in records[0].error_summary


def test_configured_app_passcode_uses_environment_then_secrets() -> None:
    assert configured_app_passcode(
        environ={"APP_PASSCODE": "from-env"},
        secrets={"APP_PASSCODE": "from-secrets"},
    ) == "from-env"
    assert configured_app_passcode(
        environ={},
        secrets={"APP_PASSCODE": "from-secrets"},
    ) == "from-secrets"
    assert configured_app_passcode(environ={}, secrets={}) == ""


def test_app_access_is_open_without_passcode_and_checks_configured_code() -> None:
    assert app_access_granted("", "")
    assert app_access_granted("", "anything")
    assert app_access_granted("demo-pass", "demo-pass")
    assert not app_access_granted("demo-pass", "wrong")


def test_recent_run_rows_uses_owner_visible_status_and_metrics() -> None:
    created = datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
    rows = recent_run_rows(
        [
            RunRecord(
                run_id="run-1",
                owner_id="owner-a",
                status="partial",
                data_mode="official",
                metrics={"valid_jobs": 3, "failed_pages": 1},
                error_summary="1 page failed",
                created_at=created,
                updated_at=created,
                retention_deadline=created + timedelta(days=RETENTION_DAYS),
            )
        ]
    )

    assert rows == [
        {
            "运行 ID": "run-1",
            "状态": "部分完成",
            "数据模式": "official",
            "有效岗位": 3,
            "创建时间": "2026-09-21 08:00",
        }
    ]


def test_build_configured_persistence_defaults_to_local_files(tmp_path) -> None:
    run_store, artifact_store = build_configured_persistence(
        tmp_path,
        environ={},
    )

    assert isinstance(run_store, FileRunStore)
    assert isinstance(artifact_store, FileArtifactStore)


def test_build_configured_persistence_rejects_partial_cloud_config(tmp_path) -> None:
    with pytest.raises(ValueError, match="OBJECT_STORAGE_BUCKET"):
        build_configured_persistence(
            tmp_path,
            environ={"DATABASE_URL": "postgresql://example"},
        )


def test_build_configured_persistence_uses_injected_cloud_factories(tmp_path) -> None:
    initialized: list[str] = []

    class FakeRunStore:
        def initialize(self) -> None:
            initialized.append("yes")

    class FakeArtifactStore:
        pass

    run_store, artifact_store = build_configured_persistence(
        tmp_path,
        environ={
            "DATABASE_URL": "postgresql://example",
            "OBJECT_STORAGE_ENDPOINT": "https://objects.example",
            "OBJECT_STORAGE_BUCKET": "private-bucket",
            "OBJECT_STORAGE_ACCESS_KEY": "access",
            "OBJECT_STORAGE_SECRET_KEY": "secret",
        },
        postgres_factory=lambda _url: FakeRunStore(),
        s3_factory=lambda **_kwargs: FakeArtifactStore(),
    )

    assert isinstance(run_store, FakeRunStore)
    assert isinstance(artifact_store, FakeArtifactStore)
    assert initialized == ["yes"]


def test_streamlit_app_enforces_passcode_before_showing_history(monkeypatch, tmp_path) -> None:
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("APP_PASSCODE", "apptest-pass")
    monkeypatch.setenv("PERSISTENCE_ROOT", str(tmp_path / "persistence"))

    app = AppTest.from_file(REPO_ROOT / "streamlit_app.py").run(timeout=20)
    assert not app.exception
    assert [item.label for item in app.text_input] == ["访问口令"]

    app.text_input[0].input("apptest-pass")
    app.button[0].click()
    app.run(timeout=20)

    assert not app.exception
    assert app.session_state["app_access_granted"] is True
    assert len(app.segmented_control) == 1


def test_streamlit_app_renders_dual_column_workspace(monkeypatch, tmp_path) -> None:
    from streamlit.testing.v1 import AppTest

    monkeypatch.delenv("APP_PASSCODE", raising=False)
    monkeypatch.setenv("PERSISTENCE_ROOT", str(tmp_path / "persistence"))

    app = AppTest.from_file(REPO_ROOT / "streamlit_app.py").run(timeout=20)
    rendered_markdown = "\n".join(item.value for item in app.markdown)

    assert not app.exception
    assert "配置任务" in rendered_markdown
    assert "运行结果" in rendered_markdown
    assert "ui-workspace-config" in rendered_markdown
    assert "ui-result-shell" in rendered_markdown
