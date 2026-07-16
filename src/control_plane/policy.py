from __future__ import annotations

import re
from datetime import UTC, datetime

from control_plane.domain import (
    ActionName,
    CaseRequest,
    DeterministicDecision,
    EvidenceRecord,
    PolicyCitation,
)

POLICIES = (
    PolicyCitation(
        policy_id="POL-KYC-004",
        version="2026.07",
        section="4.2 Fresh KYC",
        text="A transfer may be released only when identity verification is current and verified.",
        score=1.0,
        effective_at=datetime(2026, 7, 1, tzinfo=UTC),
    ),
    PolicyCitation(
        policy_id="POL-AML-011",
        version="2026.07",
        section="3.1 Open alerts",
        text="An open high-severity AML alert requires a hold and blocks transfer release.",
        score=1.0,
        effective_at=datetime(2026, 7, 1, tzinfo=UTC),
    ),
    PolicyCitation(
        policy_id="POL-EFFECT-002",
        version="2026.07",
        section="5.4 Irreversible effects",
        text=(
            "A release requires human approval bound to the exact action and effect status "
            "lookup after ambiguity."
        ),
        score=1.0,
        effective_at=datetime(2026, 7, 1, tzinfo=UTC),
    ),
    PolicyCitation(
        policy_id="POL-TENANT-001",
        version="2026.07",
        section="2.2 Isolation",
        text="Evidence and effects must remain within the authenticated tenant boundary.",
        score=1.0,
        effective_at=datetime(2026, 7, 1, tzinfo=UTC),
    ),
    PolicyCitation(
        policy_id="POL-CASE-007",
        version="2026.07",
        section="6.3 Closure",
        text=(
            "A case closes only after the requested effect is verified in an authoritative system."
        ),
        score=1.0,
        effective_at=datetime(2026, 7, 1, tzinfo=UTC),
    ),
)

INJECTION_PATTERNS = (
    re.compile(r"ignore (all|any|the|previous)", re.I),
    re.compile(r"system prompt", re.I),
    re.compile(r"bypass (policy|approval|tenant)", re.I),
    re.compile(r"call (the )?tool directly", re.I),
    re.compile(r"reveal .*tenant", re.I),
)


def retrieve_policies(request: CaseRequest, limit: int = 5) -> list[PolicyCitation]:
    """Deterministic fallback used in CI; production mirrors these records in pgvector."""
    wanted = {"POL-TENANT-001", "POL-EFFECT-002", "POL-CASE-007"}
    if request.requested_action == ActionName.RELEASE_TRANSFER:
        wanted.update({"POL-KYC-004", "POL-AML-011"})
    if request.anomaly_type == "kyc_expired":
        wanted.add("POL-KYC-004")
    if request.anomaly_type == "aml_alert":
        wanted.add("POL-AML-011")
    ranked = [policy for policy in POLICIES if policy.policy_id in wanted]
    return ranked[:limit]


def detect_untrusted_instruction(text: str) -> bool:
    return any(pattern.search(text) for pattern in INJECTION_PATTERNS)


def deterministic_decision(
    request: CaseRequest,
    records: list[EvidenceRecord],
    authenticated_tenant: str,
) -> DeterministicDecision:
    reasons: list[str] = []
    if request.tenant_id != authenticated_tenant:
        return DeterministicDecision(
            allowed=False,
            action=request.requested_action,
            reason_codes=("TENANT_MISMATCH",),
            stop=True,
        )
    if detect_untrusted_instruction(request.notes):
        return DeterministicDecision(
            allowed=False,
            action=request.requested_action,
            reason_codes=("UNTRUSTED_INSTRUCTION",),
            stop=True,
        )
    if not records:
        return DeterministicDecision(
            allowed=False,
            action=request.requested_action,
            reason_codes=("MISSING_AUTHORITATIVE_RECORDS",),
            stop=True,
        )
    if any(record.tenant_id != request.tenant_id for record in records):
        return DeterministicDecision(
            allowed=False,
            action=request.requested_action,
            reason_codes=("CROSS_TENANT_EVIDENCE",),
            stop=True,
        )
    if any(not record.authoritative for record in records):
        reasons.append("NON_AUTHORITATIVE_RECORD")
    if any(not record.fresh for record in records):
        reasons.append("STALE_RECORD")

    facts: dict[str, object] = {}
    conflicts: set[str] = set()
    for record in records:
        for key, value in record.facts.items():
            if key in facts and facts[key] != value:
                conflicts.add(key)
            facts[key] = value
    if conflicts:
        reasons.append("CONFLICTING_RECORDS")

    if request.requested_action == ActionName.RELEASE_TRANSFER:
        if facts.get("kyc_status") != "verified":
            reasons.append("KYC_NOT_VERIFIED")
        if bool(facts.get("high_severity_aml_open")):
            reasons.append("HIGH_SEVERITY_AML_OPEN")
        if facts.get("transfer_status") not in {"held", "pending_review"}:
            reasons.append("TRANSFER_NOT_RELEASABLE")
    elif request.requested_action == ActionName.REMOVE_HOLD:
        if facts.get("kyc_status") != "verified" or bool(facts.get("high_severity_aml_open")):
            reasons.append("HOLD_CONDITION_NOT_CLEARED")
    elif request.requested_action == ActionName.CLOSE_CASE:
        if not bool(facts.get("outcome_verified")):
            reasons.append("OUTCOME_NOT_VERIFIED")

    blocking = tuple(dict.fromkeys(reasons))
    return DeterministicDecision(
        allowed=not blocking,
        action=request.requested_action,
        reason_codes=blocking or ("POLICY_CONDITIONS_MET",),
        stop=bool(blocking),
    )
