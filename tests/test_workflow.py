from __future__ import annotations

from control_plane.auth import Actor
from control_plane.domain import (
    ApprovalDecision,
    CaseRequest,
    ProposedAction,
    RunStatus,
)
from control_plane.model_provider import DeterministicRecommendationProvider
from control_plane.repository import MemoryRepository
from control_plane.service import ControlPlaneService
from control_plane.tool_gateway import SyntheticToolGateway
from control_plane.workflow import ControlledWorkflow, action_hash


def case_request(**updates: object) -> CaseRequest:
    data: dict[str, object] = {
        "tenant_id": "northstar-bank",
        "customer_id": "cust-workflow-001",
        "transfer_id": "txn-workflow-001",
        "anomaly_type": "profile_mismatch",
        "requested_action": "release_transfer",
        "amount": 15000,
    }
    data.update(updates)
    return CaseRequest.model_validate(data)


def setup() -> tuple[ControlPlaneService, SyntheticToolGateway, Actor]:
    repo = MemoryRepository()
    gateway = SyntheticToolGateway()
    workflow = ControlledWorkflow(repo, gateway, DeterministicRecommendationProvider())
    service = ControlPlaneService(repo, workflow)
    actor = Actor(
        "workflow-approver",
        "northstar-bank",
        frozenset({"viewer", "operator", "approver"}),
    )
    return service, gateway, actor


async def approve_pending(service: ControlPlaneService, run_id, actor: Actor):
    run = service.repository.get_run(run_id)
    assert run is not None
    action = ProposedAction.model_validate(run.state["proposed_action"])
    return await service.approve(
        run.run_id,
        ApprovalDecision(
            approved=True,
            actor_id=actor.subject,
            proposed_action_hash=action_hash(action),
        ),
        actor,
    )


async def test_graph_interrupts_before_effect_and_resumes_from_checkpoint() -> None:
    service, gateway, actor = setup()
    case = service.create_case(case_request(), actor)
    pending = await service.start_run(case.case_id, actor)
    assert pending.status == RunStatus.AWAITING_APPROVAL
    assert gateway.effects == {}
    assert pending.state["__interrupt__"][0]["irreversible"] is True

    completed = await approve_pending(service, pending.run_id, actor)
    assert completed.status == RunStatus.COMPLETED
    assert completed.state["outcome_verified"] is True
    assert len(gateway.effects) == 1


async def test_aml_alert_stops_without_human_or_tool_effect() -> None:
    service, gateway, actor = setup()
    case = service.create_case(case_request(anomaly_type="aml_alert"), actor)
    run = await service.start_run(case.case_id, actor)
    assert run.status == RunStatus.BLOCKED
    assert "HIGH_SEVERITY_AML_OPEN" in run.state["blocked_reason"]
    assert gateway.effects == {}


async def test_transient_failure_recovers_with_same_idempotency_key() -> None:
    service, gateway, actor = setup()
    gateway.set_fault("txn-retry", "429")
    case = service.create_case(case_request(transfer_id="txn-retry"), actor)
    pending = await service.start_run(case.case_id, actor)
    completed = await approve_pending(service, pending.run_id, actor)
    assert completed.status == RunStatus.COMPLETED
    assert completed.state["retry_count"] == 1
    assert len(gateway.effects) == 1


async def test_irreversible_post_commit_timeout_uses_status_lookup() -> None:
    service, gateway, actor = setup()
    gateway.set_fault("txn-post-commit", "commit_then_timeout")
    case = service.create_case(case_request(transfer_id="txn-post-commit"), actor)
    pending = await service.start_run(case.case_id, actor)
    completed = await approve_pending(service, pending.run_id, actor)
    assert completed.status == RunStatus.COMPLETED
    assert gateway.attempts[next(iter(gateway.effects))] == 1


async def test_forged_approval_hash_closes_blocked() -> None:
    service, gateway, actor = setup()
    case = service.create_case(case_request(), actor)
    pending = await service.start_run(case.case_id, actor)
    blocked = await service.approve(
        pending.run_id,
        ApprovalDecision(
            approved=True,
            actor_id=actor.subject,
            proposed_action_hash="0" * 64,
        ),
        actor,
    )
    assert blocked.status == RunStatus.BLOCKED
    assert blocked.state["blocked_reason"] == "APPROVAL_ACTION_HASH_MISMATCH"
    assert gateway.effects == {}
