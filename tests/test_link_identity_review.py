from __future__ import annotations

from web_task_agent.link_verification import _native_ids, _redirect_identity_ok


def test_shared_year_does_not_preserve_a_different_job_id() -> None:
    requested = "https://jobs.example.com/internship/2027/position/111111/detail"
    final = "https://jobs.example.com/internship/2027/position/999999/detail"

    assert _native_ids(requested) == {"111111"}
    assert _native_ids(final) == {"999999"}
    assert _redirect_identity_ok(requested, final) is False


def test_query_job_id_mismatch_is_not_hidden_by_a_shared_year_path() -> None:
    requested = "https://jobs.example.com/campus/2027/open?jobId=alpha111"
    final = "https://jobs.example.com/campus/2027/detail?jobId=beta222"

    assert _redirect_identity_ok(requested, final) is False


def test_native_ids_support_non_numeric_slugs_and_path_or_query_ids() -> None:
    requested = "https://jobs.example.com/careers/ai-platform-engineer/positions/12345?jobId=role-alpha"
    final = "https://jobs.example.com/open/positions/12345?utm_source=email&jobId=role-alpha"

    assert _redirect_identity_ok(requested, final) is True
    assert _redirect_identity_ok(
        requested,
        "https://jobs.example.com/open/positions/12345?jobId=role-beta",
    ) is False


def test_no_native_id_requires_tracking_normalized_path_query_and_fragment_match() -> None:
    requested = "https://jobs.example.com/jobs/ai-platform-engineer?department=ai#overview"
    same = "https://jobs.example.com/jobs/ai-platform-engineer?utm_source=email&department=ai#overview"

    assert _redirect_identity_ok(requested, same) is True
    assert _redirect_identity_ok(
        requested,
        "https://jobs.example.com/jobs/ai-platform-engineer?page=2&department=ai#overview",
    ) is False
    assert _redirect_identity_ok(
        requested,
        "https://jobs.example.com/home",
    ) is False
