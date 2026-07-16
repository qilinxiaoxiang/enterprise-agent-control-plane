from __future__ import annotations

import os
from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from control_plane.auth import Actor
from control_plane.config import Settings
from control_plane.domain import (
    ActionName,
    ApprovalDecision,
    CaseRequest,
    ProposedAction,
    RunStatus,
)
from control_plane.migrations import run_migrations
from control_plane.model_provider import DeterministicRecommendationProvider
from control_plane.repository import PostgresRepository
from control_plane.retrieval import PgVectorPolicyRetriever
from control_plane.service import ControlPlaneService
from control_plane.tool_gateway import McpToolGateway, PostgresSyntheticToolGateway
from control_plane.workflow import ControlledWorkflow, action_hash

pytestmark = pytest.mark.integration


def integration_urls() -> tuple[str, str]:
    database = os.environ.get("INTEGRATION_DATABASE_URL")
    mcp = os.environ.get("INTEGRATION_MCP_URL")
    if not database or not mcp:
        pytest.skip("integration services are not configured")
    return database, mcp


@pytest.mark.asyncio
async def test_pgvector_retrieval_and_restart_safe_effect_ledger() -> None:
    database, _ = integration_urls()
    run_migrations(database)
    settings = Settings(
        app_env="test",
        repository_backend="postgres",
        database_url=database,
        otel_exporter_otlp_endpoint=None,
    )
    retriever = PgVectorPolicyRetriever(settings)
    await retriever.seed()
    request = CaseRequest(
        tenant_id="northstar-bank",
        customer_id=f"cust-{uuid4().hex[:8]}",
        transfer_id=f"txn-{uuid4().hex[:8]}",
        anomaly_type="profile_mismatch",
        requested_action="release_transfer",
        amount=18000,
    )
    policies = await retriever.retrieve(request)
    assert {policy.policy_id for policy in policies} >= {"POL-KYC-004", "POL-AML-011"}

    gateway = PostgresSyntheticToolGateway(database)
    action = ProposedAction(
        action=ActionName.PLACE_HOLD,
        tenant_id=request.tenant_id,
        case_id=uuid4(),
        transfer_id=request.transfer_id,
        reason="Integration test hold",
        idempotency_key=f"integration:{uuid4()}",
        irreversible=False,
    )
    first = await gateway.execute(action)
    second = await PostgresSyntheticToolGateway(database).execute(action)
    assert first.effect_id == second.effect_id
    assert second.status == "replayed"


@pytest.mark.asyncio
async def test_checkpoint_resumes_after_control_process_recreation_over_real_mcp() -> None:
    database, mcp_url = integration_urls()
    run_migrations(database)
    checkpoint_url = (
        f"{database}?options=-csearch_path%3Dlanggraph_checkpoints%2Cpublic"
        if "?" not in database
        else f"{database}&options=-csearch_path%3Dlanggraph_checkpoints%2Cpublic"
    )
    repository = PostgresRepository(database)
    retriever = PgVectorPolicyRetriever(
        Settings(
            app_env="test",
            repository_backend="postgres",
            database_url=database,
            otel_exporter_otlp_endpoint=None,
        )
    )
    await retriever.seed()
    gateway = McpToolGateway(mcp_url)
    actor = Actor(
        "integration-approver",
        "northstar-bank",
        frozenset({"viewer", "operator", "approver"}),
    )
    request = CaseRequest(
        tenant_id=actor.tenant_id,
        customer_id=f"cust-{uuid4().hex[:8]}",
        transfer_id=f"txn-{uuid4().hex[:8]}",
        anomaly_type="profile_mismatch",
        requested_action="release_transfer",
        amount=24000,
    )

    async with AsyncPostgresSaver.from_conn_string(checkpoint_url) as first_saver:
        await first_saver.setup()
        first_service = ControlPlaneService(
            repository,
            ControlledWorkflow(
                repository,
                gateway,
                DeterministicRecommendationProvider(),
                checkpointer=first_saver,
                retriever=retriever,
            ),
        )
        case = first_service.create_case(request, actor)
        pending = await first_service.start_run(case.case_id, actor)
        assert pending.status == RunStatus.AWAITING_APPROVAL

    proposed = ProposedAction.model_validate(pending.state["proposed_action"])
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url) as second_saver:
        second_service = ControlPlaneService(
            repository,
            ControlledWorkflow(
                repository,
                gateway,
                DeterministicRecommendationProvider(),
                checkpointer=second_saver,
                retriever=retriever,
            ),
        )
        completed = await second_service.approve(
            pending.run_id,
            ApprovalDecision(
                approved=True,
                actor_id=actor.subject,
                proposed_action_hash=action_hash(proposed),
            ),
            actor,
        )
    assert completed.status == RunStatus.COMPLETED
    assert completed.state["outcome_verified"] is True
