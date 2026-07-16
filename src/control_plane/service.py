from __future__ import annotations

from typing import Any
from uuid import UUID

from control_plane.auth import Actor
from control_plane.domain import (
    ApprovalDecision,
    CaseRequest,
    CaseState,
    ReplayRequest,
    RunEvent,
    RunRecord,
    RunStatus,
)
from control_plane.repository import Repository
from control_plane.workflow import ControlledWorkflow


class ControlPlaneService:
    def __init__(self, repository: Repository, workflow: ControlledWorkflow) -> None:
        self.repository = repository
        self.workflow = workflow

    def create_case(self, request: CaseRequest, actor: Actor, public: bool = False) -> CaseState:
        if request.tenant_id != actor.tenant_id:
            raise PermissionError("actor tenant does not match request tenant")
        case = CaseState(request=request, is_public_demo=public)
        self.repository.create_case(case)
        return case

    async def start_run(
        self, case_id: UUID, actor: Actor, replay_of: UUID | None = None
    ) -> RunRecord:
        case = self.repository.get_case(case_id)
        if case is None:
            raise KeyError(case_id)
        if case.request.tenant_id != actor.tenant_id:
            raise PermissionError("cross-tenant run denied")
        run = RunRecord(
            case_id=case_id,
            tenant_id=case.request.tenant_id,
            status=RunStatus.RUNNING,
            is_public_demo=case.is_public_demo,
            replay_of=replay_of,
        )
        self.repository.create_run(run)
        state = await self.workflow.start(
            {
                "run_id": str(run.run_id),
                "authenticated_tenant": actor.tenant_id,
                "case": case.request.model_dump(mode="json"),
            }
        )
        status = self._status(state)
        return self.repository.update_run(
            run.run_id,
            status,
            self._serializable_state(state),
            finished=status in {RunStatus.COMPLETED, RunStatus.BLOCKED, RunStatus.FAILED},
        )

    async def approve(self, run_id: UUID, approval: ApprovalDecision, actor: Actor) -> RunRecord:
        run = self._authorized_run(run_id, actor)
        if run.status != RunStatus.AWAITING_APPROVAL:
            raise ValueError("run is not awaiting approval")
        state = await self.workflow.resume(run_id, approval)
        status = self._status(state)
        return self.repository.update_run(
            run_id,
            status,
            self._serializable_state(state),
            finished=status in {RunStatus.COMPLETED, RunStatus.BLOCKED, RunStatus.FAILED},
        )

    async def replay(self, run_id: UUID, request: ReplayRequest, actor: Actor) -> RunRecord:
        original = self._authorized_run(run_id, actor)
        self.repository.append_event(
            RunEvent(
                run_id=run_id,
                event_type="replay",
                node=request.from_node or "intake_validation",
                message=f"Replay requested: {request.reason}",
            )
        )
        return await self.start_run(original.case_id, actor, replay_of=run_id)

    def _authorized_run(self, run_id: UUID, actor: Actor) -> RunRecord:
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.tenant_id != actor.tenant_id:
            raise PermissionError("cross-tenant run denied")
        return run

    @staticmethod
    def _status(state: dict[str, Any]) -> RunStatus:
        if state.get("__interrupt__"):
            return RunStatus.AWAITING_APPROVAL
        if state.get("current_node") == "audit_closure" and state.get("outcome_verified"):
            return RunStatus.COMPLETED
        if state.get("current_node") == "blocked_closure" or state.get("blocked_reason"):
            return RunStatus.BLOCKED
        return RunStatus.RUNNING

    @staticmethod
    def _serializable_state(state: dict[str, Any]) -> dict[str, Any]:
        result = dict(state)
        if "__interrupt__" in result:
            result["__interrupt__"] = [
                getattr(item, "value", str(item)) for item in result["__interrupt__"]
            ]
        return result
