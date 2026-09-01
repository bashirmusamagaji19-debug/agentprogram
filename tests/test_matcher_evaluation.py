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


# ── hybrid 口径与 LLM 复用（#27）─────────────────────────────────────


@pytest.mark.asyncio
async def test_hybrid_column_present_and_uses_same_llm_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """混合口径列要出现在报告里，且与纯 LLM 口径共享同一次 LLM 响应（#27）。"""
    ground_truth = tmp_path / "gt.jsonl"
    # 规则低分样本（无交集）：触发 LLM 兜底路径
    ground_truth.write_text(
        json.dumps(
            {
                "id": 1,
                "job_skills": ["Selenium", "测试"],
                "user_skills": ["Python"],
                "resume_text": "",
                "label": "no_match",
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
    args.llm_match = True

    exit_code = await run_matcher_evaluation(args)

    assert exit_code == 0
    # 纯 LLM 口径 1 次 + 混合口径复用同一响应，不得重复调用（#27）
    assert len(calls) == 1
    report = (tmp_path / "eval" / "matcher-evaluation.md").read_text(encoding="utf-8")
    assert "混合匹配准确率" in report


@pytest.mark.asyncio
async def test_hybrid_denominator_excludes_llm_error_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """LLM 出错行被剔除后，hybrid 准确率分母与展示分母必须一致（#27）。"""
    ground_truth = tmp_path / "gt.jsonl"
    ground_truth.write_text(
        "\n".join(
            json.dumps(
                {
                    "id": i,
                    "job_skills": ["Python"],
                    "user_skills": ["Python"],
                    "resume_text": "",
                    "label": "match",
                },
                ensure_ascii=False,
            )
            for i in (1, 2)
        ),
        encoding="utf-8",
    )
    state = {"n": 0}

    def flaky_llm(payload: dict) -> dict:
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("api down")
        return {"score": 0.9, "reason": "", "matched_skills": [], "missing_skills": []}

    monkeypatch.setattr(
        "web_task_agent.cli.build_cli_llm_matcher", lambda args: flaky_llm
    )
    args = make_args(tmp_path, ground_truth)
    args.llm_match = True

    exit_code = await run_matcher_evaluation(args)

    assert exit_code == 0
    report = (tmp_path / "eval" / "matcher-evaluation.md").read_text(encoding="utf-8")
    # 第 1 条 LLM 失败被剔除：hybrid 分母应为 1 而不是 total=2
    assert "(1/1)" in report
