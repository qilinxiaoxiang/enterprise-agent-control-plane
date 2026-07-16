from __future__ import annotations

import asyncio
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row

from control_plane.domain import (
    ActionName,
    CaseRequest,
    EvidenceRecord,
    ProposedAction,
    ToolReceipt,
)
from control_plane.telemetry import span


class ToolFailure(RuntimeError):
    def __init__(
        self, kind: str, message: str, *, retryable: bool, committed: bool = False
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.committed = committed


class ToolGateway(Protocol):
    async def collect(self, request: CaseRequest) -> list[EvidenceRecord]: ...
    async def execute(self, action: ProposedAction) -> ToolReceipt: ...
    async def status(self, idempotency_key: str) -> ToolReceipt: ...


class SyntheticToolGateway:
    """Authoritative synthetic systems plus deterministic failure injection."""

    def __init__(self) -> None:
        self.effects: dict[str, ToolReceipt] = {}
        self.attempts: dict[str, int] = defaultdict(int)
        self.faults: dict[str, str] = {}
        self.records_override: dict[str, list[EvidenceRecord]] = {}

    def set_fault(self, transfer_id: str, fault: str) -> None:
        self.faults[transfer_id] = fault

    def set_records(self, transfer_id: str, records: list[EvidenceRecord]) -> None:
        self.records_override[transfer_id] = records

    async def collect(self, request: CaseRequest) -> list[EvidenceRecord]:
        with span("mcp.collect_authoritative_records", tenant_id=request.tenant_id):
            if request.transfer_id in self.records_override:
                return self.records_override[request.transfer_id]
            now = datetime.now(UTC)
            kyc_status = "expired" if request.anomaly_type == "kyc_expired" else "verified"
            aml_open = request.anomaly_type == "aml_alert"
            transfer_status = (
                "held"
                if request.requested_action == ActionName.RELEASE_TRANSFER
                else "pending_review"
            )
            return [
                EvidenceRecord(
                    tenant_id=request.tenant_id,
                    source="customer-profile",
                    record_id=f"profile:{request.customer_id}",
                    observed_at=now,
                    expires_at=now + timedelta(days=30),
                    facts={"customer_id": request.customer_id, "profile_status": "active"},
                ),
                EvidenceRecord(
                    tenant_id=request.tenant_id,
                    source="kyc-aml",
                    record_id=f"kyc:{request.customer_id}",
                    observed_at=now,
                    expires_at=now + timedelta(days=1),
                    facts={
                        "kyc_status": kyc_status,
                        "high_severity_aml_open": aml_open,
                    },
                ),
                EvidenceRecord(
                    tenant_id=request.tenant_id,
                    source="ledger",
                    record_id=f"transfer:{request.transfer_id}",
                    observed_at=now,
                    facts={
                        "transfer_status": transfer_status,
                        "amount": request.amount,
                        "currency": request.currency,
                    },
                ),
            ]

    def _receipt(self, action: ProposedAction, attempts: int) -> ToolReceipt:
        return ToolReceipt(
            tool_name=action.action,
            idempotency_key=action.idempotency_key,
            status="succeeded",
            effect_id=f"effect-{uuid4().hex[:12]}",
            response={"verified": True, "action": action.action, "tenant_id": action.tenant_id},
            attempt_count=attempts,
            committed_at=datetime.now(UTC),
        )

    async def execute(self, action: ProposedAction) -> ToolReceipt:
        key = action.idempotency_key
        self.attempts[key] += 1
        attempt = self.attempts[key]
        with span(
            "mcp.tool",
            tool_name=action.action,
            idempotency_key=key,
            attempt=attempt,
        ):
            if key in self.effects:
                return self.effects[key].model_copy(
                    update={"status": "replayed", "attempt_count": attempt}
                )
            fault = self.faults.get(action.transfer_id)
            if fault in {"timeout", "429", "5xx"} and attempt == 1:
                raise ToolFailure(fault, f"injected {fault}", retryable=True)
            if fault == "schema_drift":
                raise ToolFailure(
                    fault, "tool response failed contract validation", retryable=False
                )
            if fault == "commit_then_timeout" and attempt == 1:
                receipt = self._receipt(action, attempt)
                self.effects[key] = receipt
                raise ToolFailure(
                    fault, "connection lost after commit", retryable=False, committed=True
                )
            receipt = self._receipt(action, attempt)
            self.effects[key] = receipt
            return receipt

    async def status(self, idempotency_key: str) -> ToolReceipt:
        with span("mcp.effect_status", idempotency_key=idempotency_key):
            return self.effects.get(
                idempotency_key,
                ToolReceipt(
                    tool_name="get_action_status",
                    idempotency_key=idempotency_key,
                    status="not_found",
                ),
            )


class PostgresSyntheticToolGateway(SyntheticToolGateway):
    """Synthetic source records with a restart-safe PostgreSQL effect ledger."""

    def __init__(self, database_url: str) -> None:
        super().__init__()
        self.database_url = database_url

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self.database_url, row_factory=dict_row)

    @staticmethod
    def _from_row(row: dict[str, Any], replayed: bool = False) -> ToolReceipt:
        return ToolReceipt(
            invocation_id=row["id"],
            tool_name=row["tool_name"],
            idempotency_key=row["idempotency_key"],
            status="replayed" if replayed else row["status"],
            effect_id=row["effect_id"],
            response=row["response"],
            committed_at=row["committed_at"],
        )

    async def execute(self, action: ProposedAction) -> ToolReceipt:
        payload = json.dumps(action.model_dump(mode="json"), sort_keys=True)
        request_hash = hashlib.sha256(payload.encode()).hexdigest()
        invocation_id = uuid4()
        effect_id = f"effect-{uuid4().hex[:12]}"
        response = {
            "verified": True,
            "action": action.action,
            "tenant_id": action.tenant_id,
        }
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM effect_ledger WHERE idempotency_key=%s",
                (action.idempotency_key,),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise ToolFailure(
                        "idempotency_conflict",
                        "idempotency key was already bound to a different action",
                        retryable=False,
                    )
                return self._from_row(existing, replayed=True)
            row = connection.execute(
                """INSERT INTO effect_ledger
                   (id, tenant_id, tool_name, idempotency_key, status, effect_id,
                    request_hash, response, committed_at)
                   VALUES (%s,%s,%s,%s,'succeeded',%s,%s,%s,now())
                   ON CONFLICT (idempotency_key) DO NOTHING RETURNING *""",
                (
                    invocation_id,
                    action.tenant_id,
                    action.action,
                    action.idempotency_key,
                    effect_id,
                    request_hash,
                    json.dumps(response),
                ),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM effect_ledger WHERE idempotency_key=%s",
                    (action.idempotency_key,),
                ).fetchone()
                if row is None:
                    raise ToolFailure("ledger_race", "effect ledger race", retryable=True)
                return self._from_row(row, replayed=True)
            return self._from_row(row)

    async def status(self, idempotency_key: str) -> ToolReceipt:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM effect_ledger WHERE idempotency_key=%s",
                (idempotency_key,),
            ).fetchone()
        if row:
            return self._from_row(row)
        return ToolReceipt(
            tool_name="get_action_status",
            idempotency_key=idempotency_key,
            status="not_found",
        )


class McpToolGateway:
    def __init__(self, server_url: str, audience: str | None = None) -> None:
        self.server_url = server_url
        self.audience = audience

    async def _headers(self) -> dict[str, str] | None:
        if not self.audience:
            return None
        from google.auth.transport.requests import Request
        from google.oauth2.id_token import fetch_id_token

        token = await asyncio.to_thread(fetch_id_token, Request(), self.audience)
        return {"Authorization": f"Bearer {token}"}

    async def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        with span("mcp.tool", tool_name=name):
            async with httpx.AsyncClient(headers=await self._headers(), timeout=30) as client:
                async with streamable_http_client(self.server_url, http_client=client) as (
                    read,
                    write,
                    _,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(name, arguments)
        if result.isError:
            raise ToolFailure("mcp_error", str(result.content), retryable=True)
        for block in result.content:
            text = getattr(block, "text", None)
            if text:
                parsed = json.loads(text)
                if not isinstance(parsed, dict):
                    raise ToolFailure(
                        "invalid_response", "MCP tool JSON was not an object", retryable=False
                    )
                return cast(dict[str, Any], parsed)
        raise ToolFailure("empty_response", "MCP tool returned no JSON text", retryable=False)

    async def collect(self, request: CaseRequest) -> list[EvidenceRecord]:
        records: list[EvidenceRecord] = []
        for tool, arguments in (
            (
                "get_customer_profile",
                {"tenant_id": request.tenant_id, "customer_id": request.customer_id},
            ),
            ("get_kyc_aml", {"tenant_id": request.tenant_id, "customer_id": request.customer_id}),
            ("get_transfer", {"tenant_id": request.tenant_id, "transfer_id": request.transfer_id}),
        ):
            result = await self._call(tool, arguments)
            records.append(EvidenceRecord.model_validate(result))
        return records

    async def execute(self, action: ProposedAction) -> ToolReceipt:
        try:
            result = await self._call(action.action, action.model_dump(mode="json"))
        except ToolFailure:
            raise
        return ToolReceipt.model_validate(result)

    async def status(self, idempotency_key: str) -> ToolReceipt:
        result = await self._call("get_action_status", {"idempotency_key": idempotency_key})
        return ToolReceipt.model_validate(result)
