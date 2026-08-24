from pathlib import Path


def test_readme_uses_open_search_metric_boundaries():
    text = Path("README.md").read_text(encoding="utf-8")
    assert "官方/可信 ATS" in text or "可信招聘详情页" in text
    assert "fixture" in text.lower()
    assert "真实" in text
