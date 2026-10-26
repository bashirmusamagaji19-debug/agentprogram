from web_task_agent.model_value_dataset import _GROUPS, _candidates, _revision_record
from web_task_agent.model_value_runner import _known_removed_page
from web_task_agent.models import BrowserPage


def test_v4_related_recommendation_is_review_without_changing_v3_or_deliveries():
    group = next(group for group in _GROUPS if group.number == 3)
    for variant in ("v1", "v2"):
        old = _candidates(group, variant, dataset_id="model-value-v3")
        revised = _candidates(group, variant, dataset_id="model-value-v4")
        assert old[-1].decision == "rejected"
        assert revised[-1].decision == "review"
        assert revised[-1].body == old[-1].body
        assert [c.job_key for c in revised if c.decision == "accepted"] == [
            c.job_key for c in old if c.decision == "accepted"]
    other = next(group for group in _GROUPS if group.number == 15)
    assert _candidates(other, "v1", dataset_id="model-value-v4")[-1].decision == "rejected"


def test_v4_revision_records_human_approved_scope():
    revision = _revision_record("model-value-v4")
    assert revision["supersedes_dataset_id"] == "model-value-v3"
    assert revision["changed_frozen_labels_in_place"] is False


def test_transport_error_does_not_imply_known_removal():
    assert not _known_removed_page(BrowserPage(url="https://example.com/1", content="Request timed out"))
    assert _known_removed_page(BrowserPage(url="https://example.com/1",
        content="Status: This job posting has been removed."))
