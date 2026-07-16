from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

AnomalyType = Literal[
    "profile_mismatch", "kyc_expired", "aml_alert", "unusual_amount", "duplicate_request"
]


def utcnow() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActionName(StrEnum):
    PLACE_HOLD = "place_hold"
    REMOVE_HOLD = "remove_hold"
    RELEASE_TRANSFER = "release_transfer"
    CLOSE_CASE = "close_case"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class CaseRequest(StrictModel):
    tenant_id: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9-]+$")
    customer_id: str = Field(min_length=3, max_length=64)
    transfer_id: str = Field(min_length=3, max_length=64)
    anomaly_type: AnomalyType
    requested_action: ActionName
    amount: float = Field(gt=0, le=10_000_000)
    currency: Literal["USD", "EUR", "GBP"] = "USD"
    notes: str = Field(default="", max_length=2_000)
    source: str = Field(default="case-management", max_length=100)


class CaseState(StrictModel):
    case_id: UUID = Field(default_factory=uuid4)
    request: CaseRequest
    created_at: datetime = Field(default_factory=utcnow)
    status: Literal["open", "closed"] = "open"
    is_public_demo: bool = False


class EvidenceRecord(StrictModel):
    tenant_id: str
    source: Literal["customer-profile", "kyc-aml", "ledger", "case-management"]
    record_id: str
    observed_at: datetime
    expires_at: datetime | None = None
    authoritative: bool = True
    facts: dict[str, Any]

    @property
    def fresh(self) -> bool:
        return self.expires_at is None or self.expires_at >= utcnow()


class PolicyCitation(StrictModel):
    policy_id: str
    version: str
    section: str
    text: str
    score: float = Field(ge=0, le=1)
    effective_at: datetime


class DeterministicDecision(StrictModel):
    allowed: bool
    requires_human: bool = True
    action: ActionName
    reason_codes: tuple[str, ...]
    stop: bool = False


class Recommendation(StrictModel):
    recommended_action: ActionName
    rationale: str = Field(max_length=1_200)
    cited_policy_ids: tuple[str, ...]
    unsupported_claims: tuple[str, ...] = ()
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0, ge=0)


class ProposedAction(StrictModel):
    action: ActionName
    tenant_id: str
    case_id: UUID
    transfer_id: str
    reason: str
    idempotency_key: str = Field(min_length=8, max_length=128)
    irreversible: bool


class ApprovalDecision(StrictModel):
    approved: bool
    actor_id: str = Field(min_length=2, max_length=128)
    actor_role: Literal["approver"] = "approver"
    comment: str = Field(default="", max_length=1_000)
    proposed_action_hash: str = Field(min_length=16)
    decided_at: datetime = Field(default_factory=utcnow)


class ToolReceipt(StrictModel):
    invocation_id: UUID = Field(default_factory=uuid4)
    tool_name: str
    idempotency_key: str
    status: Literal["succeeded", "replayed", "failed", "ambiguous", "compensated", "not_found"]
    effect_id: str | None = None
    response: dict[str, Any] = Field(default_factory=dict)
    attempt_count: int = Field(default=1, ge=1)
    committed_at: datetime | None = None


class RunEvent(StrictModel):
    event_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    event_type: str
    node: str
    message: str
    timestamp: datetime = Field(default_factory=utcnow)
    payload: dict[str, Any] = Field(default_factory=dict)


class RunRecord(StrictModel):
    run_id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    tenant_id: str
    status: RunStatus = RunStatus.QUEUED
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    is_public_demo: bool = False
    replay_of: UUID | None = None


class EvalResult(StrictModel):
    case_id: str
    category: str
    runner: Literal["baseline", "controlled"]
    task_success: bool
    recovered: bool | None = None
    unsafe_write: bool = False
    unauthorized_write: bool = False
    cross_tenant_leakage: bool = False
    duplicate_side_effect: bool = False
    unsupported_claims: int = Field(default=0, ge=0)
    retrieval_hits_at_5: int = Field(default=0, ge=0, le=5)
    retrieval_relevant: int = Field(default=1, ge=1)
    latency_ms: float = Field(ge=0)
    model_cost_usd: float = Field(ge=0)
    detail: str = ""


class WebhookEnvelope(StrictModel):
    event_id: str = Field(min_length=8, max_length=128)
    case: CaseRequest


class ReplayRequest(StrictModel):
    from_node: str | None = None
    reason: str = Field(min_length=3, max_length=500)


class ApprovalRequest(StrictModel):
    approved: bool
    comment: str = Field(default="", max_length=1_000)
    proposed_action_hash: str = Field(min_length=16)


class MetricsSummary(StrictModel):
    total_runs: int
    success_rate: float
    error_rate: float
    p95_latency_ms: float
    average_model_cost_usd: float
    recovery_rate: float
    unsafe_writes: int
    duplicate_side_effects: int
    source: Literal["seeded-demo", "evaluation", "live"]


class EvalCase(StrictModel):
    id: str
    category: Literal[
        "happy_path", "record_integrity", "transient_failure", "security", "idempotency_replay"
    ]
    request: CaseRequest
    expected_outcome: Literal["execute", "block", "manual_review"]
    expected_action: ActionName
    fault: str | None = None
    attack: str | None = None
    expected_policy_ids: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_scenario(self) -> EvalCase:
        if self.category == "transient_failure" and not self.fault:
            raise ValueError("transient_failure cases require fault")
        if self.category == "security" and not self.attack:
            raise ValueError("security cases require attack")
        return self
