from datetime import UTC, datetime

import pytest

from web_task_agent.agent_approval import (
    ApprovalAuditEvent,
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalStatus,
)
from web_task_agent.agent_models import DecisionAgentState
from web_task_agent.models import UserProfile


def test_approval_request_exposes_only_redacted_summary_fields():
    request = ApprovalRequest(
        approval_id="approval-1",
        thread_id="demo-1",
        requested_at=datetime(2026, 7, 29, tzinfo=UTC),
        job_count=2,
        summary="Persist 2 verified job records.",
    )

    assert request.status is ApprovalStatus.PENDING
    assert set(request.public_payload()) == {
        "approval_id",
        "thread_id",
        "action",
        "requested_at",
        "job_count",
        "summary",
        "status",
    }


def test_approval_request_rejects_naive_requested_at():
    with pytest.raises(ValueError, match="timezone-aware"):
        ApprovalRequest(
            approval_id="approval-1",
            thread_id="demo-1",
            requested_at=datetime(2026, 7, 29),
            job_count=1,
            summary="Persist 1 verified job record.",
        )


def test_approval_decision_rejects_blank_id_and_overlong_note():
    with pytest.raises(ValueError, match="approval_id"):
        ApprovalDecision(approval_id=" ", outcome=ApprovalOutcome.APPROVE)
    with pytest.raises(ValueError, match="at most 500"):
        ApprovalDecision(
            approval_id="approval-1",
            outcome=ApprovalOutcome.REJECT,
            note="x" * 501,
        )


def test_audit_event_requires_timezone_aware_timestamp():
    with pytest.raises(ValueError, match="timezone-aware"):
        ApprovalAuditEvent(
            approval_id="approval-1",
            event="requested",
            occurred_at=datetime(2026, 7, 29),
        )


def test_decision_agent_approval_defaults_are_isolated():
    first = DecisionAgentState(user=UserProfile(keyword="AI intern"))
    second = DecisionAgentState(user=UserProfile(keyword="AI intern"))

    assert first.execution_id != second.execution_id
    first.approval_audit.append(
        ApprovalAuditEvent(
            approval_id="approval-1",
            event="requested",
            occurred_at=datetime(2026, 7, 29, tzinfo=UTC),
        )
    )

    assert second.approval_audit == []
