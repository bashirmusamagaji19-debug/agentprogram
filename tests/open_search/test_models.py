import re

import pytest
from pydantic import ValidationError

from web_task_agent.open_search.models import (
    FailureRecord,
    FieldEvidence,
    SearchCandidate,
    SearchIntent,
    SearchRunSummary,
    VerifiedJob,
)


def test_search_intent_normalizes_unique_constraints():
    intent = SearchIntent(
        raw_text=" 找北京 Agent 实习 ",
        role_keywords=["Agent", "agent"],
        locations=["北京", "北京"],
        required_skills=[" Python ", "python"],
        preferred_skills=[" LangGraph ", "langgraph"],
        excluded_roles=[" 产品经理 ", "产品经理"],
        target_count=5,
    )

    assert intent.raw_text == "找北京 Agent 实习"
    assert intent.role_keywords == ["Agent"]
    assert intent.locations == ["北京"]
    assert intent.required_skills == ["Python"]
    assert intent.preferred_skills == ["LangGraph"]
    assert intent.excluded_roles == ["产品经理"]


@pytest.mark.parametrize("target_count", [0, 21])
def test_search_intent_restricts_target_count(target_count):
    with pytest.raises(ValidationError):
        SearchIntent(raw_text="Agent", target_count=target_count)


def test_failure_record_requires_code_and_url():
    with pytest.raises(ValidationError):
        FailureRecord(code="page_unavailable", url="", message="timeout")


def test_models_trim_strings_and_dedupe_lists():
    candidate = SearchCandidate(
        url=" https://example.com/jobs/1 ",
        title=" Agent Intern ",
        snippet=" snippet ",
        source=" fixture ",
        tags=["AI", " ai ", "Python"],
    )
    evidence = FieldEvidence(
        field_name=" title ",
        value=" Agent Intern ",
        snippet=" evidence ",
        page_url=" https://example.com/jobs/1 ",
        content_hash="abcdef1234567890",
    )
    job = VerifiedJob(
        title=" Agent Intern ",
        company=" Example AI ",
        location=" Beijing ",
        url=" https://example.com/jobs/1 ",
        source=" fixture ",
        skills=["Python", " python "],
        evidence=[evidence, evidence],
    )

    assert candidate.url == "https://example.com/jobs/1"
    assert candidate.tags == ["AI", "Python"]
    assert evidence.field_name == "title"
    assert evidence.page_url == "https://example.com/jobs/1"
    assert job.title == "Agent Intern"
    assert job.skills == ["Python"]
    assert len(job.evidence) == 1


def test_content_hash_is_non_empty_hexadecimal():
    evidence = FieldEvidence(
        field_name="title",
        value="Agent Intern",
        snippet="Agent Intern",
        page_url="https://example.com/jobs/1",
        content_hash="ABCDEF1234567890",
    )
    assert re.fullmatch(r"[0-9a-fA-F]+", evidence.content_hash)

    with pytest.raises(ValidationError):
        FieldEvidence(
            field_name="title",
            value="Agent Intern",
            snippet="Agent Intern",
            page_url="https://example.com/jobs/1",
            content_hash="not-hex",
        )


def test_search_run_summary_tracks_terminal_state_and_counts():
    summary = SearchRunSummary(
        run_id=" run-1 ",
        target_count=2,
        candidates_seen=3,
        verified_count=2,
        failures=1,
        terminal_reason=" target_reached ",
    )
    assert summary.run_id == "run-1"
    assert summary.terminal_reason == "target_reached"
    assert summary.verified_count == 2
