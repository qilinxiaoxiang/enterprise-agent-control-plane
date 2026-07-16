from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from control_plane.domain import ActionName, AnomalyType, CaseRequest, ProposedAction
from control_plane.tool_gateway import PostgresSyntheticToolGateway, SyntheticToolGateway

database_url = os.environ.get("DATABASE_URL")
gateway = PostgresSyntheticToolGateway(database_url) if database_url else SyntheticToolGateway()
mcp = FastMCP(
    "Synthetic Financial Systems",
    instructions=(
        "Authoritative synthetic customer, KYC/AML, ledger, and case tools. "
        "Every write requires an idempotency key; authorization is enforced by the control plane."
    ),
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8081")),
    stateless_http=True,
    json_response=True,
)


def _request(
    tenant_id: str,
    customer_id: str,
    transfer_id: str,
    *,
    anomaly_type: AnomalyType = "profile_mismatch",
    requested_action: ActionName = ActionName.PLACE_HOLD,
    amount: float = 10_000,
) -> CaseRequest:
    return CaseRequest(
        tenant_id=tenant_id,
        customer_id=customer_id,
        transfer_id=transfer_id,
        anomaly_type=anomaly_type,
        requested_action=requested_action,
        amount=amount,
    )


@mcp.tool()
async def get_customer_profile(tenant_id: str, customer_id: str) -> dict[str, Any]:
    """Return one authoritative synthetic customer profile record."""
    records = await gateway.collect(_request(tenant_id, customer_id, "lookup-profile"))
    return records[0].model_dump(mode="json")


@mcp.tool()
async def get_kyc_aml(tenant_id: str, customer_id: str) -> dict[str, Any]:
    """Return current synthetic KYC status and open AML-alert state."""
    records = await gateway.collect(_request(tenant_id, customer_id, "lookup-kyc"))
    return records[1].model_dump(mode="json")


@mcp.tool()
async def get_transfer(tenant_id: str, transfer_id: str) -> dict[str, Any]:
    """Return an authoritative synthetic ledger transfer record."""
    records = await gateway.collect(_request(tenant_id, "lookup-transfer", transfer_id))
    return records[2].model_dump(mode="json")


async def _effect(action_name: ActionName, payload: dict[str, Any]) -> dict[str, Any]:
    action = ProposedAction.model_validate({**payload, "action": action_name})
    receipt = await gateway.execute(action)
    return receipt.model_dump(mode="json")


@mcp.tool()
async def place_hold(
    tenant_id: str,
    case_id: str,
    transfer_id: str,
    reason: str,
    idempotency_key: str,
    irreversible: bool = False,
) -> dict[str, Any]:
    """Place a compensatable hold through the idempotent effect ledger."""
    return await _effect(ActionName.PLACE_HOLD, locals())


@mcp.tool()
async def remove_hold(
    tenant_id: str,
    case_id: str,
    transfer_id: str,
    reason: str,
    idempotency_key: str,
    irreversible: bool = False,
) -> dict[str, Any]:
    """Remove a hold through the idempotent effect ledger."""
    return await _effect(ActionName.REMOVE_HOLD, locals())


@mcp.tool()
async def release_transfer(
    tenant_id: str,
    case_id: str,
    transfer_id: str,
    reason: str,
    idempotency_key: str,
    irreversible: bool = True,
) -> dict[str, Any]:
    """Release a synthetic transfer; this irreversible effect must never be blindly retried."""
    return await _effect(ActionName.RELEASE_TRANSFER, locals())


@mcp.tool()
async def close_case(
    tenant_id: str,
    case_id: str,
    transfer_id: str,
    reason: str,
    idempotency_key: str,
    irreversible: bool = False,
) -> dict[str, Any]:
    """Close a synthetic case after authoritative outcome verification."""
    return await _effect(ActionName.CLOSE_CASE, locals())


@mcp.tool()
async def get_action_status(idempotency_key: str) -> dict[str, Any]:
    """Query the authoritative effect ledger after an ambiguous write response."""
    return (await gateway.status(idempotency_key)).model_dump(mode="json")
