import pytest

from web_task_agent.evaluation import EvaluationRunner, build_default_tasks


def test_build_default_tasks_returns_twenty_tasks():
    tasks = build_default_tasks()

    assert len(tasks) == 20
    assert tasks[0].keyword
    assert tasks[0].target_count >= 1


@pytest.mark.asyncio
async def test_evaluation_runner_computes_summary(tmp_path):
    runner = EvaluationRunner(output_dir=tmp_path)

    result = await runner.run(tasks=build_default_tasks()[:3])

    assert result.total_tasks == 3
    assert result.completed_tasks == 3
    assert result.success_rate == 1.0
    assert result.total_valid_jobs >= 3
    assert result.average_pages_visited > 0
    assert result.report_path is not None
    assert result.report_path.exists()
    assert "任务成功率" in result.report_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_evaluation_runner_records_failed_task(tmp_path):
    task = build_default_tasks()[0].model_copy(update={"required_keywords": ["quantum"]})
    runner = EvaluationRunner(output_dir=tmp_path)

    result = await runner.run(tasks=[task])

    assert result.total_tasks == 1
    assert result.completed_tasks == 0
    assert result.success_rate == 0.0
    assert result.task_results[0].failure_reason == "no valid jobs"
