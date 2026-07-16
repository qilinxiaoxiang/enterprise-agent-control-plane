from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Protocol
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from control_plane.domain import (
    ApprovalDecision,
    CaseState,
    RunEvent,
    RunRecord,
    RunStatus,
    ToolReceipt,
)


class Repository(Protocol):
    def create_case(self, case: CaseState) -> None: ...
    def get_case(self, case_id: UUID) -> CaseState | None: ...
    def create_run(self, run: RunRecord) -> None: ...
    def get_run(self, run_id: UUID) -> RunRecord | None: ...
    def list_runs(self, public_only: bool = False) -> list[RunRecord]: ...
    def update_run(
        self, run_id: UUID, status: RunStatus, state: dict[str, Any], finished: bool = False
    ) -> RunRecord: ...
    def append_event(self, event: RunEvent) -> None: ...
    def events(self, run_id: UUID, after: datetime | None = None) -> list[RunEvent]: ...
    def save_approval(self, run_id: UUID, approval: ApprovalDecision) -> None: ...
    def save_receipt(self, run_id: UUID, receipt: ToolReceipt) -> None: ...
    def claim_webhook(self, event_id: str) -> bool: ...


class MemoryRepository:
    def __init__(self) -> None:
        self.cases: dict[UUID, CaseState] = {}
        self.runs: dict[UUID, RunRecord] = {}
        self.run_events: dict[UUID, list[RunEvent]] = defaultdict(list)
        self.approvals: dict[UUID, list[ApprovalDecision]] = defaultdict(list)
        self.receipts: dict[UUID, list[ToolReceipt]] = defaultdict(list)
        self.webhooks: set[str] = set()
        self._lock = RLock()

    def create_case(self, case: CaseState) -> None:
        with self._lock:
            self.cases[case.case_id] = case

    def get_case(self, case_id: UUID) -> CaseState | None:
        return self.cases.get(case_id)

    def create_run(self, run: RunRecord) -> None:
        with self._lock:
            self.runs[run.run_id] = run

    def get_run(self, run_id: UUID) -> RunRecord | None:
        return self.runs.get(run_id)

    def list_runs(self, public_only: bool = False) -> list[RunRecord]:
        runs: Iterable[RunRecord] = self.runs.values()
        if public_only:
            runs = (run for run in runs if run.is_public_demo)
        return sorted(runs, key=lambda run: run.started_at, reverse=True)

    def update_run(
        self, run_id: UUID, status: RunStatus, state: dict[str, Any], finished: bool = False
    ) -> RunRecord:
        with self._lock:
            previous = self.runs[run_id]
            updated = previous.model_copy(
                update={
                    "status": status,
                    "state": state,
                    "finished_at": datetime.now(UTC) if finished else previous.finished_at,
                }
            )
            self.runs[run_id] = updated
            return updated

    def append_event(self, event: RunEvent) -> None:
        with self._lock:
            self.run_events[event.run_id].append(event)

    def events(self, run_id: UUID, after: datetime | None = None) -> list[RunEvent]:
        events = self.run_events.get(run_id, [])
        if after:
            events = [event for event in events if event.timestamp > after]
        return list(events)

    def save_approval(self, run_id: UUID, approval: ApprovalDecision) -> None:
        with self._lock:
            self.approvals[run_id].append(approval)

    def save_receipt(self, run_id: UUID, receipt: ToolReceipt) -> None:
        with self._lock:
            self.receipts[run_id].append(receipt)

    def claim_webhook(self, event_id: str) -> bool:
        with self._lock:
            if event_id in self.webhooks:
                return False
            self.webhooks.add(event_id)
            return True


class PostgresRepository:
    """Small explicit repository; LangGraph checkpoints live in their own schema."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def create_case(self, case: CaseState) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO cases (id, tenant_id, request, status, is_public_demo)
                   VALUES (%s,%s,%s,%s,%s)""",
                (
                    case.case_id,
                    case.request.tenant_id,
                    json.dumps(case.request.model_dump(mode="json")),
                    case.status,
                    case.is_public_demo,
                ),
            )

    def get_case(self, case_id: UUID) -> CaseState | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM cases WHERE id=%s", (case_id,)).fetchone()
        if not row:
            return None
        return CaseState(
            case_id=row["id"],
            request=row["request"],
            created_at=row["created_at"],
            status=row["status"],
            is_public_demo=row["is_public_demo"],
        )

    def create_run(self, run: RunRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO runs
                   (id, case_id, tenant_id, status, state, is_public_demo, replay_of)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (
                    run.run_id,
                    run.case_id,
                    run.tenant_id,
                    run.status,
                    json.dumps(run.state),
                    run.is_public_demo,
                    run.replay_of,
                ),
            )

    @staticmethod
    def _run(row: dict[str, Any]) -> RunRecord:
        return RunRecord(
            run_id=row["id"],
            case_id=row["case_id"],
            tenant_id=row["tenant_id"],
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            state=row["state"],
            is_public_demo=row["is_public_demo"],
            replay_of=row["replay_of"],
        )

    def get_run(self, run_id: UUID) -> RunRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=%s", (run_id,)).fetchone()
        return self._run(row) if row else None

    def list_runs(self, public_only: bool = False) -> list[RunRecord]:
        where = "WHERE is_public_demo" if public_only else ""
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM runs {where} ORDER BY started_at DESC").fetchall()
        return [self._run(row) for row in rows]

    def update_run(
        self, run_id: UUID, status: RunStatus, state: dict[str, Any], finished: bool = False
    ) -> RunRecord:
        finished_at = datetime.now(UTC) if finished else None
        with self._connect() as conn:
            row = conn.execute(
                """UPDATE runs SET status=%s, state=%s,
                   finished_at=COALESCE(%s, finished_at) WHERE id=%s RETURNING *""",
                (status, json.dumps(state), finished_at, run_id),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._run(row)

    def append_event(self, event: RunEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO run_events
                   (id, run_id, event_type, node, message, payload, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (
                    event.event_id,
                    event.run_id,
                    event.event_type,
                    event.node,
                    event.message,
                    json.dumps(event.payload),
                    event.timestamp,
                ),
            )

    def events(self, run_id: UUID, after: datetime | None = None) -> list[RunEvent]:
        query = "SELECT * FROM run_events WHERE run_id=%s"
        params: list[Any] = [run_id]
        if after:
            query += " AND created_at>%s"
            params.append(after)
        query += " ORDER BY created_at"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            RunEvent(
                event_id=row["id"],
                run_id=row["run_id"],
                event_type=row["event_type"],
                node=row["node"],
                message=row["message"],
                timestamp=row["created_at"],
                payload=row["payload"],
            )
            for row in rows
        ]

    def save_approval(self, run_id: UUID, approval: ApprovalDecision) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO approvals
                   (run_id, approved, actor_id, actor_role, comment, action_hash, decided_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (
                    run_id,
                    approval.approved,
                    approval.actor_id,
                    approval.actor_role,
                    approval.comment,
                    approval.proposed_action_hash,
                    approval.decided_at,
                ),
            )

    def save_receipt(self, run_id: UUID, receipt: ToolReceipt) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO tool_invocations
                   (id, run_id, tool_name, idempotency_key, status, effect_id, response,
                    attempt_count, committed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    receipt.invocation_id,
                    run_id,
                    receipt.tool_name,
                    receipt.idempotency_key,
                    receipt.status,
                    receipt.effect_id,
                    json.dumps(receipt.response),
                    receipt.attempt_count,
                    receipt.committed_at,
                ),
            )

    def claim_webhook(self, event_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """INSERT INTO webhook_receipts (event_id) VALUES (%s)
                   ON CONFLICT DO NOTHING RETURNING event_id""",
                (event_id,),
            ).fetchone()
        return row is not None
