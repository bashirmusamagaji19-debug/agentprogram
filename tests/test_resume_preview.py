from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from web_task_agent.streamlit_app import (
    RESUME_PREVIEW_TEXT_KEY,
    RESUME_UPLOAD_BLOCKED_KEY,
    resume_run_blocker,
    sync_resume_preview,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeUpload:
    def __init__(self, name: str, content: bytes) -> None:
        self.name = name
        self._content = content

    def getvalue(self) -> bytes:
        return self._content


def _pdf(text: str = "Python RAG internship", password: str | None = None) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/Type1"),
                            NameObject("/BaseFont"): NameObject("/Helvetica"),
                        }
                    )
                }
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_resume_preview_tracks_upload_replacement_and_removal() -> None:
    state: dict[str, object] = {}
    first = FakeUpload("first.pdf", _pdf("First resume Python"))

    sync_resume_preview(state, first)
    assert "First resume Python" in str(state[RESUME_PREVIEW_TEXT_KEY])
    assert resume_run_blocker(state) == ""

    state[RESUME_PREVIEW_TEXT_KEY] = "用户手工修订后的简历"
    sync_resume_preview(state, FakeUpload("first.pdf", _pdf("First resume Python")))
    assert state[RESUME_PREVIEW_TEXT_KEY] == "用户手工修订后的简历"

    sync_resume_preview(state, FakeUpload("second.pdf", _pdf("Second resume LangGraph")))
    assert "Second resume LangGraph" in str(state[RESUME_PREVIEW_TEXT_KEY])
    assert "First resume" not in str(state[RESUME_PREVIEW_TEXT_KEY])

    sync_resume_preview(state, None)
    assert state[RESUME_PREVIEW_TEXT_KEY] == ""
    assert resume_run_blocker(state) == ""


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("scan.pdf", _pdf(""), "扫描|文字"),
        ("locked.pdf", _pdf("Private resume", password="fixture-only"), "加密|解密"),
        ("broken.pdf", b"%PDF-1.7 not really a pdf", "损坏|无法读取"),
    ],
)
def test_bad_resume_clears_previous_preview_and_blocks_run(
    name: str, content: bytes, message: str
) -> None:
    state: dict[str, object] = {}
    sync_resume_preview(state, FakeUpload("good.pdf", _pdf("Good previous resume")))
    assert "Good previous resume" in str(state[RESUME_PREVIEW_TEXT_KEY])

    sync_resume_preview(state, FakeUpload(name, content))

    assert state[RESUME_PREVIEW_TEXT_KEY] == ""
    assert state[RESUME_UPLOAD_BLOCKED_KEY] is True
    assert re.search(message, resume_run_blocker(state)) is not None

    sync_resume_preview(state, None)
    assert resume_run_blocker(state) == ""

def test_app_pdf_upload_can_be_previewed_edited_replaced_and_removed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(REPO_ROOT / "streamlit_app.py"), default_timeout=30).run()
    assert not app.exception

    uploader = next(item for item in app.file_uploader if item.label == "简历文件")
    uploader.set_value(("first.pdf", _pdf("First resume Python"), "application/pdf"))
    app.run()
    assert not app.exception
    preview = next(item for item in app.text_area if item.label == "简历文字预览")
    assert "First resume Python" in preview.value

    preview.set_value("用户编辑：Python + LangGraph")
    app.run()
    preview = next(item for item in app.text_area if item.label == "简历文字预览")
    assert preview.value == "用户编辑：Python + LangGraph"

    uploader = next(item for item in app.file_uploader if item.label == "简历文件")
    uploader.set_value(("second.pdf", _pdf("Second resume RAG"), "application/pdf"))
    app.run()
    preview = next(item for item in app.text_area if item.label == "简历文字预览")
    assert "Second resume RAG" in preview.value
    assert "用户编辑" not in preview.value

    uploader = next(item for item in app.file_uploader if item.label == "简历文件")
    uploader.set_value(None)
    app.run()
    preview = next(item for item in app.text_area if item.label == "简历文字预览")
    assert preview.value == ""


def test_app_shows_unreadable_upload_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(REPO_ROOT / "streamlit_app.py"), default_timeout=30).run()
    uploader = next(item for item in app.file_uploader if item.label == "简历文件")
    uploader.set_value(("scan.pdf", _pdf(""), "application/pdf"))
    app.run()

    assert not app.exception
    assert any("扫描" in item.value or "文字" in item.value for item in app.error)
    preview = next(item for item in app.text_area if item.label == "简历文字预览")
    assert preview.value == ""


def test_app_bad_pdf_blocks_start_even_after_previous_preview(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(REPO_ROOT / "streamlit_app.py"), default_timeout=30).run()
    next(
        item for item in app.segmented_control if item.label == "数据模式"
    ).set_value("内置 Demo")
    app.run()
    uploader = next(item for item in app.file_uploader if item.label == "简历文件")
    uploader.set_value(("good.pdf", _pdf("Previous resume Python"), "application/pdf"))
    app.run()
    assert "Previous resume Python" in next(
        item for item in app.text_area if item.label == "简历文字预览"
    ).value

    uploader = next(item for item in app.file_uploader if item.label == "简历文件")
    uploader.set_value(("broken.pdf", b"%PDF-1.7 broken", "application/pdf"))
    app.run()
    next(button for button in app.button if button.label == "开始运行岗位 Agent").click()
    app.run()

    assert not app.exception
    assert "latest_run_id" not in app.session_state
    assert any("本次运行未启动" in item.value for item in app.error)
