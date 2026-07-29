from __future__ import annotations

from enum import StrEnum
from math import isfinite
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from web_task_agent.agent_approval import ApprovalAuditEvent, ApprovalRequest
from web_task_agent.models import BrowserPage, JobPosting, MatchResult, UserProfile


class AgentAction(StrEnum):
    SEARCH_JOBS = "search_jobs"
    OPEN_PAGE = "open_page"
    EXTRACT_TEXT = "extract_text"
    EXTRACT_VISUAL = "extract_visual"
    VERIFY_JOB = "verify_job"
    SCORE_MATCH = "score_match"
    SAVE_RESULTS = "save_results"
    FINISH = "finish"


class DecisionSource(StrEnum):
    LLM = "llm"
    POLICY = "policy"
    FALLBACK = "fallback"


class AgentDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: AgentAction
    reason: str
    target: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: DecisionSource = DecisionSource.POLICY

    @field_validator("reason")
    @classmethod
    def require_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("decision reason must not be empty")
        return value

    @field_validator("confidence")
    @classmethod
    def require_finite_confidence(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("decision confidence must be finite")
        return value


class ToolObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_name: AgentAction
    success: bool
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    error_category: str = ""
    error_message: str = ""
    latency_ms: float = Field(default=0.0, ge=0.0)
    recoverable: bool = False

    @model_validator(mode="after")
    def require_error_category_for_failure(self) -> ToolObservation:
        if not self.success and not self.error_category.strip():
            raise ValueError("failed observation requires error_category")
        return self


class AgentBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_steps: int = Field(default=12, ge=1, le=100)
    consumed_steps: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def clamp_consumed_steps(self) -> AgentBudget:
        if self.consumed_steps > self.max_steps:
            return self.model_copy(update={"consumed_steps": self.max_steps})
        return self

    @property
    def remaining_steps(self) -> int:
        return max(self.max_steps - self.consumed_steps, 0)

    @property
    def exhausted(self) -> bool:
        return self.remaining_steps == 0

    def consume(self) -> AgentBudget:
        return self.model_copy(
            update={"consumed_steps": min(self.consumed_steps + 1, self.max_steps)}
        )


class AgentMetrics(BaseModel):
    tool_calls: int = Field(default=0, ge=0)
    successful_tool_calls: int = Field(default=0, ge=0)
    recovery_attempts: int = Field(default=0, ge=0)
    successful_recoveries: int = Field(default=0, ge=0)
    planner_calls: int = Field(default=0, ge=0)
    fallback_decisions: int = Field(default=0, ge=0)
    invalid_actions: int = Field(default=0, ge=0)
    total_latency_ms: float = Field(default=0.0, ge=0.0)

    @property
    def tool_success_rate(self) -> float:
        return self.successful_tool_calls / self.tool_calls if self.tool_calls else 0.0

    @property
    def recovery_success_rate(self) -> float:
        return (
            self.successful_recoveries / self.recovery_attempts if self.recovery_attempts else 0.0
        )

    @property
    def fallback_rate(self) -> float:
        return self.fallback_decisions / self.planner_calls if self.planner_calls else 0.0

    @property
    def average_tool_latency_ms(self) -> float:
        return self.total_latency_ms / self.tool_calls if self.tool_calls else 0.0


class DecisionAgentState(BaseModel):
    user: UserProfile
    execution_id: str = Field(default_factory=lambda: uuid4().hex)
    budget: AgentBudget = Field(default_factory=AgentBudget)
    candidate_urls: list[str] = Field(default_factory=list)
    visited_urls: set[str] = Field(default_factory=set)
    current_url: str | None = None
    current_page: BrowserPage | None = None
    extracted_jobs: list[JobPosting] = Field(default_factory=list)
    verified_jobs: list[JobPosting] = Field(default_factory=list)
    matches: list[MatchResult] = Field(default_factory=list)
    saved: bool = False
    visual_available: bool = False
    last_decision: AgentDecision | None = None
    last_observation: ToolObservation | None = None
    decision_history: list[AgentDecision] = Field(default_factory=list)
    observation_history: list[ToolObservation] = Field(default_factory=list)
    retry_counts: dict[str, int] = Field(default_factory=dict)
    metrics: AgentMetrics = Field(default_factory=AgentMetrics)
    recovery_in_progress: bool = False
    hitl_enabled: bool = False
    thread_id: str = ""
    pending_approval: ApprovalRequest | None = None
    approval_audit: list[ApprovalAuditEvent] = Field(default_factory=list)
    terminal_status: str = "running"
    terminal_reason: str = ""
