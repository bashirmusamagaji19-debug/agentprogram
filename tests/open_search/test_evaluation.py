from web_task_agent.open_search.evaluation import evaluate_frozen_queries


def test_evaluation_reports_separate_metric_families():
    report = evaluate_frozen_queries("data/open-search/evaluation/queries.jsonl")
    assert report.query_count == 20
    assert report.metric_families == ["offline_frozen", "online_audit"]
    assert report.hard_constraint_violations == 0
