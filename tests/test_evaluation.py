import pytest

from web_task_agent.browser import BrowserConfigurationError
from web_task_agent import evaluation as evaluation_module
from web_task_agent.evaluation import EvaluationRunner, build_default_tasks
from web_task_agent.models import BrowserPage


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


@pytest.mark.asyncio
async def test_evaluation_runner_reports_failure_category_distribution(tmp_path):
    task = build_default_tasks()[0].model_copy(update={"required_keywords": ["quantum"]})
    runner = EvaluationRunner(output_dir=tmp_path)

    result = await runner.run(tasks=[task])

    assert result.task_results[0].failure_category == "verification_filtered"
    assert result.failure_counts == {"verification_filtered": 1}
    report = result.report_path.read_text(encoding="utf-8")
    assert "失败原因分布" in report
    assert "verification_filtered" in report


@pytest.mark.asyncio
async def test_evaluation_runner_classifies_browser_errors(tmp_path):
    class FailingBrowser:
        async def search(self, query: str, target_count: int) -> list[BrowserPage]:
            raise BrowserConfigurationError("blocked by site")

        async def open_url(self, url: str) -> BrowserPage:
            raise AssertionError("open_url should not be called")

    runner = EvaluationRunner(
        output_dir=tmp_path,
        browser_factory=lambda task: FailingBrowser(),
    )

    result = await runner.run(tasks=[build_default_tasks()[0]])

    task_result = result.task_results[0]
    assert task_result.success is False
    assert task_result.failure_category == "browser_error"
    assert task_result.failure_reason == "browser error"
    assert task_result.failure_details == "blocked by site"
    assert result.failure_counts == {"browser_error": 1}


@pytest.mark.asyncio
async def test_evaluation_runner_classifies_no_pages(tmp_path):
    class EmptyBrowser:
        async def search(self, query: str, target_count: int) -> list[BrowserPage]:
            return []

        async def open_url(self, url: str) -> BrowserPage:
            raise AssertionError("open_url should not be called")

    runner = EvaluationRunner(
        output_dir=tmp_path,
        browser_factory=lambda task: EmptyBrowser(),
    )

    result = await runner.run(tasks=[build_default_tasks()[0]])

    assert result.task_results[0].failure_category == "no_pages"
    assert result.task_results[0].failure_reason == "no pages returned"


def test_build_real_smoke_tasks_returns_small_public_search_set():
    tasks = evaluation_module.build_real_smoke_tasks()

    assert 1 <= len(tasks) <= 5
    assert all(task.target_count == 1 for task in tasks)
    assert any("AI" in task.keyword for task in tasks)
