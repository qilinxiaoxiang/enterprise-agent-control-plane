from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from control_plane.domain import (
    ActionName,
    AnomalyType,
    CaseRequest,
    CaseState,
    RunEvent,
    RunRecord,
    RunStatus,
)
from control_plane.repository import Repository


def seed_public_runs(repository: Repository) -> None:
    if repository.list_runs(public_only=True):
        return
    examples: tuple[tuple[str, AnomalyType, ActionName, RunStatus], ...] = (
        (
            "transfer-release-verified",
            "profile_mismatch",
            ActionName.RELEASE_TRANSFER,
            RunStatus.COMPLETED,
        ),
        ("aml-alert-safe-stop", "aml_alert", ActionName.RELEASE_TRANSFER, RunStatus.BLOCKED),
        (
            "commit-timeout-recovered",
            "unusual_amount",
            ActionName.RELEASE_TRANSFER,
            RunStatus.COMPLETED,
        ),
    )
    now = datetime.now(UTC)
    for index, (name, anomaly, action, status) in enumerate(examples):
        case = CaseState(
            request=CaseRequest(
                tenant_id="northstar-bank",
                customer_id=f"cust-demo-{index + 1:03}",
                transfer_id=f"txn-demo-{index + 1:03}",
                anomaly_type=anomaly,
                requested_action=action,
                amount=12500 + index * 7500,
                notes="Synthetic public walkthrough record.",
            ),
            is_public_demo=True,
        )
        repository.create_case(case)
        run = RunRecord(
            run_id=uuid4(),
            case_id=case.case_id,
            tenant_id=case.request.tenant_id,
            status=status,
            started_at=now - timedelta(minutes=12 - index * 4),
            finished_at=now - timedelta(minutes=11 - index * 4),
            state={
                "demo_name": name,
                "current_node": (
                    "audit_closure" if status == RunStatus.COMPLETED else "blocked_closure"
                ),
                "outcome_verified": status == RunStatus.COMPLETED,
                "blocked_reason": (
                    "HIGH_SEVERITY_AML_OPEN" if status == RunStatus.BLOCKED else None
                ),
                "retry_count": 1 if "timeout" in name else 0,
                "model_cost_usd": 0.0018 + index * 0.0002,
                "latency_ms": 2380 + index * 410,
            },
            is_public_demo=True,
        )
        repository.create_run(run)
        stages = (
            "intake_validation",
            "authoritative_record_collection",
            "policy_retrieval",
            "conflict_freshness_checks",
            "deterministic_policy_decision",
            "llm_recommendation",
            "human_interrupt",
            "idempotent_execution",
            "outcome_verification",
            "audit_closure" if status == RunStatus.COMPLETED else "blocked_closure",
        )
        for step, node in enumerate(stages):
            if status == RunStatus.BLOCKED and step > 6:
                break
            repository.append_event(
                RunEvent(
                    run_id=run.run_id,
                    node=node,
                    event_type="demo",
                    message=node.replace("_", " ").title(),
                    timestamp=run.started_at + timedelta(milliseconds=step * 230),
                )
            )


def main() -> None:
    from control_plane.app_factory import build_repository
    from control_plane.config import get_settings

    seed_public_runs(build_repository(get_settings()))
