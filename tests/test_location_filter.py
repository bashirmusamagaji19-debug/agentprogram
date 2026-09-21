import pytest

from web_task_agent.location_filter import location_matches


@pytest.mark.parametrize(
    ("requested", "actual"),
    [
        ("深圳", "深圳"),
        ("深圳", "深圳市"),
        ("深圳", "广东省-深圳市"),
        ("深圳", "深圳总部、北京、上海"),
        ("北京", "北京市、上海市"),
        ("上海", "上海市"),
        ("Remote", "远程"),
        ("远程", "Remote - China"),
        ("全国", "Unknown Location"),
        ("不限", "北京"),
    ],
)
def test_location_matches_dynamic_requested_location(requested, actual):
    assert location_matches(requested, actual) is True


@pytest.mark.parametrize(
    ("requested", "actual"),
    [
        ("深圳", "北京市"),
        ("北京", "深圳市"),
        ("上海", "Unknown Location"),
        ("深圳", ""),
        ("深圳", "Remote"),
    ],
)
def test_location_rejects_other_or_unknown_locations(requested, actual):
    assert location_matches(requested, actual) is False
