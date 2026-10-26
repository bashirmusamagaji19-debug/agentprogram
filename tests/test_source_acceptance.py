import json

import pytest

from web_task_agent.agent_acceptance import build_parser, run_acceptance


@pytest.mark.asyncio
async def test_source_verify_and_full_replay_use_only_archive(tmp_path, monkeypatch):
    from web_task_agent.source_acceptance import main
    captured = tmp_path / "capture"
    args = build_parser().parse_args(["--mode", "demo", "--output-dir", str(captured), "--run-id", "run-archive"])
    assert await run_acceptance(args) == 0
    archive = captured / "source-archive/run-archive"
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    assert main(["--mode", "verify", "--input-dir", str(archive)]) == 0
    # main manages its own event loop; call the async API in this test loop.
    from web_task_agent.source_acceptance import build_parser as source_parser
    from web_task_agent.source_acceptance import run_source_acceptance
    output = tmp_path / "replay"
    replay_args = source_parser().parse_args([
        "--mode", "replay",
        "--input-dir", str(archive),
        "--output-dir", str(output),
    ])
    assert await run_source_acceptance(replay_args) == 0
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["valid_jobs"] == summary["saved_jobs"] == 3
    assert summary["normalized_results_match"] is True
    assert summary["network_calls"] == 0
    assert summary["reader"] == "rules"
    with pytest.raises(FileExistsError):
        await run_source_acceptance(replay_args)


@pytest.mark.asyncio
async def test_changed_replay_conditions_require_explicit_reevaluation(tmp_path):
    from web_task_agent.source_acceptance import build_parser as source_parser
    from web_task_agent.source_acceptance import run_source_acceptance
    from web_task_agent.source_replay import ConditionsChangedError
    captured = tmp_path / "capture"
    await run_acceptance(build_parser().parse_args([
        "--mode", "demo",
        "--output-dir", str(captured),
        "--run-id", "run-archive",
    ]))
    archive = captured / "source-archive/run-archive"
    args = source_parser().parse_args(["--mode", "replay", "--input-dir", str(archive),
                                      "--location", "北京", "--output-dir", str(tmp_path / "changed")])
    with pytest.raises(ConditionsChangedError, match="conditions"):
        await run_source_acceptance(args)
    args.reevaluate_conditions = True
    assert await run_source_acceptance(args) == 0
    summary = json.loads((args.output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["conditions_match"] is False
    assert summary["valid_jobs"] == 0
