from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, TypedDict, cast
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from control_plane.domain import (
    ApprovalDecision,
    CaseRequest,
    DeterministicDecision,
    PolicyCitation,
    ProposedAction,
    Recommendation,
    RunEvent,
    ToolReceipt,
)
from control_plane.model_provider import RecommendationProvider
from control_plane.policy import deterministic_decision
from control_plane.repository import Repository
from control_plane.retrieval import DeterministicPolicyRetriever, PolicyRetriever
from control_plane.telemetry import span
from control_plane.tool_gateway import ToolFailure, ToolGateway


class WorkflowState(TypedDict, total=False):
    run_id: str
    authenticated_tenant: str
    case: dict[str, Any]
    evidence: list[dict[str, Any]]
    policies: list[dict[str, Any]]
    decision: dict[str, Any]
    recommendation: dict[str, Any]
    proposed_action: dict[str, Any]
    approval: dict[str, Any]
    receipt: dict[str, Any]
    outcome_verified: bool
    blocked_reason: str
    current_node: str
    retry_count: int


def action_hash(action: ProposedAction) -> str:
    payload = json.dumps(action.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class ControlledWorkflow:
    def __init__(
        self,
        repository: Repository,
        tools: ToolGateway,
        model: RecommendationProvider,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        retriever: PolicyRetriever | None = None,
    ) -> None:
        self.repository = repository
        self.tools = tools
        self.model = model
        self.retriever = retriever or DeterministicPolicyRetriever()
        self.checkpointer = checkpointer or InMemorySaver()
        self.graph = self._build().compile(checkpointer=self.checkpointer)

    def _event(
        self,
        state: WorkflowState,
        node: str,
        message: str,
        event_type: str = "node",
        **payload: Any,
    ) -> None:
        self.repository.append_event(
            RunEvent(
                run_id=UUID(state["run_id"]),
                event_type=event_type,
                node=node,
                message=message,
                payload=payload,
            )
        )

    def _build(self) -> StateGraph[WorkflowState]:
        graph = StateGraph(WorkflowState)
        graph.add_node("intake_validation", self._validate)
        graph.add_node("authoritative_record_collection", self._collect)
        graph.add_node("policy_retrieval", self._retrieve)
        graph.add_node("conflict_freshness_checks", self._check)
        graph.add_node("deterministic_policy_decision", self._decide)
        graph.add_node("llm_recommendation", self._recommend)
        graph.add_node("human_interrupt", self._human_interrupt)
        graph.add_node("idempotent_execution", self._execute)
        graph.add_node("outcome_verification", self._verify)
        graph.add_node("audit_closure", self._close)
        graph.add_node("blocked_closure", self._blocked)

        graph.add_edge(START, "intake_validation")
        graph.add_edge("intake_validation", "authoritative_record_collection")
        graph.add_edge("authoritative_record_collection", "policy_retrieval")
        graph.add_edge("policy_retrieval", "conflict_freshness_checks")
        graph.add_edge("conflict_freshness_checks", "deterministic_policy_decision")
        graph.add_edge("deterministic_policy_decision", "llm_recommendation")
        graph.add_conditional_edges(
            "llm_recommendation",
            self._after_recommendation,
            {"approve": "human_interrupt", "block": "blocked_closure"},
        )
        graph.add_conditional_edges(
            "human_interrupt",
            self._after_approval,
            {"execute": "idempotent_execution", "block": "blocked_closure"},
        )
        graph.add_edge("idempotent_execution", "outcome_verification")
        graph.add_conditional_edges(
            "outcome_verification",
            self._after_verification,
            {"close": "audit_closure", "block": "blocked_closure"},
        )
        graph.add_edge("audit_closure", END)
        graph.add_edge("blocked_closure", END)
        return graph

    async def _validate(self, state: WorkflowState) -> dict[str, Any]:
        with span("langgraph.node", node="intake_validation"):
            request = CaseRequest.model_validate(state["case"])
            if request.tenant_id != state["authenticated_tenant"]:
                raise ValueError("authenticated tenant does not match case tenant")
            self._event(state, "intake_validation", "Typed request and tenant boundary validated")
            return {"current_node": "intake_validation"}

    async def _collect(self, state: WorkflowState) -> dict[str, Any]:
        request = CaseRequest.model_validate(state["case"])
        with span("langgraph.node", node="authoritative_record_collection"):
            records = await self.tools.collect(request)
            self._event(
                state,
                "authoritative_record_collection",
                f"Collected {len(records)} authoritative source records",
                sources=[record.source for record in records],
            )
            return {
                "current_node": "authoritative_record_collection",
                "evidence": [record.model_dump(mode="json") for record in records],
            }

    async def _retrieve(self, state: WorkflowState) -> dict[str, Any]:
        request = CaseRequest.model_validate(state["case"])
        with span("retrieval.policy", node="policy_retrieval", top_k=5):
            policies = await self.retriever.retrieve(request)
            self._event(
                state,
                "policy_retrieval",
                f"Retrieved {len(policies)} versioned policy citations",
                policy_ids=[policy.policy_id for policy in policies],
            )
            return {
                "current_node": "policy_retrieval",
                "policies": [policy.model_dump(mode="json") for policy in policies],
            }

    async def _check(self, state: WorkflowState) -> dict[str, Any]:
        records = state.get("evidence", [])
        with span("langgraph.node", node="conflict_freshness_checks"):
            self._event(
                state,
                "conflict_freshness_checks",
                "Source freshness, authority, conflict, and tenant checks evaluated",
                records=len(records),
            )
            return {"current_node": "conflict_freshness_checks"}

    async def _decide(self, state: WorkflowState) -> dict[str, Any]:
        from control_plane.domain import EvidenceRecord

        request = CaseRequest.model_validate(state["case"])
        records = [EvidenceRecord.model_validate(record) for record in state.get("evidence", [])]
        with span("policy.deterministic", action=str(request.requested_action)):
            decision = deterministic_decision(request, records, state["authenticated_tenant"])
            self._event(
                state,
                "deterministic_policy_decision",
                "Deterministic authorization policy evaluated",
                allowed=decision.allowed,
                reason_codes=list(decision.reason_codes),
            )
            return {
                "current_node": "deterministic_policy_decision",
                "decision": decision.model_dump(mode="json"),
            }

    async def _recommend(self, state: WorkflowState) -> dict[str, Any]:
        request = CaseRequest.model_validate(state["case"])
        decision = DeterministicDecision.model_validate(state["decision"])
        policies = [PolicyCitation.model_validate(policy) for policy in state["policies"]]
        with span("langgraph.node", node="llm_recommendation"):
            recommendation = await self.model.recommend(request, decision, policies)
            if not set(recommendation.cited_policy_ids).issubset(
                {policy.policy_id for policy in policies}
            ):
                recommendation = recommendation.model_copy(
                    update={"unsupported_claims": ("uncited_policy_reference",)}
                )
            run = self.repository.get_run(UUID(state["run_id"]))
            if run is None:
                raise RuntimeError("run disappeared before recommendation")
            action = ProposedAction(
                action=request.requested_action,
                tenant_id=request.tenant_id,
                case_id=run.case_id,
                transfer_id=request.transfer_id,
                reason=recommendation.rationale,
                idempotency_key=f"{state['run_id']}:{request.requested_action}",
                irreversible=request.requested_action.value == "release_transfer",
            )
            self._event(
                state,
                "llm_recommendation",
                "Bound recommendation to retrieved citations and exact proposed action",
                unsupported_claims=len(recommendation.unsupported_claims),
            )
            return {
                "current_node": "llm_recommendation",
                "recommendation": recommendation.model_dump(mode="json"),
                "proposed_action": action.model_dump(mode="json"),
            }

    @staticmethod
    def _after_recommendation(state: WorkflowState) -> str:
        decision = DeterministicDecision.model_validate(state["decision"])
        recommendation = Recommendation.model_validate(state["recommendation"])
        return "approve" if decision.allowed and not recommendation.unsupported_claims else "block"

    async def _human_interrupt(self, state: WorkflowState) -> dict[str, Any]:
        action = ProposedAction.model_validate(state["proposed_action"])
        expected_hash = action_hash(action)
        self._event(
            state,
            "human_interrupt",
            "Paused before consequential effect",
            "approval",
            action_hash=expected_hash,
            irreversible=action.irreversible,
        )
        with span("approval.interrupt", action=str(action.action)):
            payload = interrupt(
                {
                    "run_id": state["run_id"],
                    "proposed_action": action.model_dump(mode="json"),
                    "proposed_action_hash": expected_hash,
                    "irreversible": action.irreversible,
                }
            )
        approval = ApprovalDecision.model_validate(payload)
        if approval.proposed_action_hash != expected_hash:
            return {
                "approval": approval.model_dump(mode="json"),
                "blocked_reason": "APPROVAL_ACTION_HASH_MISMATCH",
                "current_node": "human_interrupt",
            }
        self.repository.save_approval(UUID(state["run_id"]), approval)
        self._event(
            state,
            "human_interrupt",
            "Approval decision recorded and bound to proposed action",
            "approval",
            approved=approval.approved,
            actor_id=approval.actor_id,
        )
        return {
            "approval": approval.model_dump(mode="json"),
            "current_node": "human_interrupt",
        }

    @staticmethod
    def _after_approval(state: WorkflowState) -> str:
        if state.get("blocked_reason"):
            return "block"
        approval = ApprovalDecision.model_validate(state["approval"])
        return "execute" if approval.approved else "block"

    async def _execute(self, state: WorkflowState) -> dict[str, Any]:
        action = ProposedAction.model_validate(state["proposed_action"])
        receipt: ToolReceipt | None = None
        last_error: ToolFailure | None = None
        with span("langgraph.node", node="idempotent_execution", action=str(action.action)):
            for attempt in range(1, 4):
                try:
                    receipt = await self.tools.execute(action)
                    break
                except ToolFailure as exc:
                    last_error = exc
                    self._event(
                        state,
                        "idempotent_execution",
                        f"Tool failure classified as {exc.kind}",
                        "retry",
                        attempt=attempt,
                        retryable=exc.retryable,
                        committed=exc.committed,
                    )
                    if action.irreversible or exc.committed:
                        receipt = await self.tools.status(action.idempotency_key)
                        if receipt.status != "not_found":
                            break
                    if not exc.retryable or attempt == 3:
                        break
                    await asyncio.sleep(0.01 * attempt)
            if receipt is None or receipt.status not in {"succeeded", "replayed"}:
                reason = last_error.kind if last_error else "EFFECT_NOT_FOUND_AFTER_AMBIGUITY"
                return {
                    "current_node": "idempotent_execution",
                    "blocked_reason": reason,
                    "outcome_verified": False,
                    "retry_count": 3 if last_error else 0,
                }
            self.repository.save_receipt(UUID(state["run_id"]), receipt)
            self._event(
                state,
                "idempotent_execution",
                "Effect receipt committed to audit ledger",
                "effect",
                status=receipt.status,
                effect_id=receipt.effect_id,
                attempts=receipt.attempt_count,
            )
            return {
                "current_node": "idempotent_execution",
                "receipt": receipt.model_dump(mode="json"),
                "retry_count": max(0, receipt.attempt_count - 1),
            }

    async def _verify(self, state: WorkflowState) -> dict[str, Any]:
        if state.get("blocked_reason") or not state.get("receipt"):
            return {"outcome_verified": False, "current_node": "outcome_verification"}
        receipt = ToolReceipt.model_validate(state["receipt"])
        verified = bool(receipt.response.get("verified")) and receipt.status in {
            "succeeded",
            "replayed",
        }
        self._event(
            state,
            "outcome_verification",
            "Authoritative effect outcome verified" if verified else "Effect outcome not verified",
            verified=verified,
        )
        return {"outcome_verified": verified, "current_node": "outcome_verification"}

    @staticmethod
    def _after_verification(state: WorkflowState) -> str:
        return "close" if state.get("outcome_verified") else "block"

    async def _close(self, state: WorkflowState) -> dict[str, Any]:
        self._event(
            state,
            "audit_closure",
            "Run closed with evidence, policy, approval, effect, and verification chain",
            "closure",
        )
        return {"current_node": "audit_closure"}

    async def _blocked(self, state: WorkflowState) -> dict[str, Any]:
        reason = state.get("blocked_reason")
        if not reason:
            decision = DeterministicDecision.model_validate(state["decision"])
            reason = ",".join(decision.reason_codes)
        self._event(state, "blocked_closure", f"Run stopped safely: {reason}", "closure")
        return {"current_node": "blocked_closure", "blocked_reason": reason}

    async def start(self, state: WorkflowState) -> dict[str, Any]:
        config: RunnableConfig = {"configurable": {"thread_id": state["run_id"]}}
        return cast(dict[str, Any], await self.graph.ainvoke(state, config=config))

    async def resume(self, run_id: UUID, approval: ApprovalDecision) -> dict[str, Any]:
        config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}
        command: Command[Any] = Command(resume=approval.model_dump(mode="json"))
        return cast(dict[str, Any], await self.graph.ainvoke(command, config=config))
