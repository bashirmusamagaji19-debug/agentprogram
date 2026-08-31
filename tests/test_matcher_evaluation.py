"""--evaluate-matcher 评测口径测试（bug 排查实录 #20）。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from web_task_agent.cli import run_matcher_evaluation


def make_args(tmp_path: Path, ground_truth: Path) -> argparse.Namespace:
    return argparse.Namespace(
        ground_truth=str(ground_truth),
        llm_match_provider=None,
        llm_match_demo=False,
        llm_match=False,
        evaluation_dir=str(tmp_path / "eval"),
        json_output=None,
    )


@pytest.mark.asyncio
async def test_llm_called_for_every_row_even_when_rule_high(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """纯 LLM 口径：规则分 ≥0.6 的样本也必须真实调 LLM，不得用规则分顶替。"""
    ground_truth = tmp_path / "gt.jsonl"
    ground_truth.write_text(
        json.dumps(
            {
                "id": 1,
                "job_skills": ["Python"],
                "user_skills": ["Python"],
                "resume_text": "",
                "label": "match",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    calls: list[dict] = []

    def fake_llm_matcher(payload: dict) -> dict:
        calls.append(payload)
        return {"score": 0.9, "reason": "", "matched_skills": [], "missing_skills": []}

    monkeypatch.setattr(
        "web_task_agent.cli.build_cli_llm_matcher", lambda args: fake_llm_matcher
    )
    args = make_args(tmp_path, ground_truth)
    args.llm_match = True  # 触发 LLM 分支

    exit_code = await run_matcher_evaluation(args)

    assert exit_code == 0
    assert len(calls) == 1  # 规则分 1.0 ≥ 0.6，旧实现这里不会调 LLM
    report = (tmp_path / "eval" / "matcher-evaluation.md").read_text(encoding="utf-8")
    assert "纯 LLM" in report


@pytest.mark.asyncio
async def test_llm_call_failure_recorded_not_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """LLM 调用失败要显式计数，不得静默回退成规则分冒充 llm_score。"""
    ground_truth = tmp_path / "gt.jsonl"
    ground_truth.write_text(
        json.dumps(
            {
                "id": 1,
                "job_skills": ["Python"],
                "user_skills": ["Python"],
                "resume_text": "",
                "label": "match",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    def broken_llm_matcher(payload: dict) -> dict:
        raise RuntimeError("api down")

    monkeypatch.setattr(
        "web_task_agent.cli.build_cli_llm_matcher", lambda args: broken_llm_matcher
    )
    args = make_args(tmp_path, ground_truth)
    args.llm_match = True

    exit_code = await run_matcher_evaluation(args)

    assert exit_code == 0
    report = (tmp_path / "eval" / "matcher-evaluation.md").read_text(encoding="utf-8")
    assert "ERR(RuntimeError)" in report
    assert "LLM 调用失败" in report
