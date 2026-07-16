from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import statistics
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from control_plane.auth import Actor, optional_actor, require_role
from control_plane.domain import (
    ApprovalDecision,
    ApprovalRequest,
    CaseRequest,
    MetricsSummary,
    ReplayRequest,
    RunRecord,
    RunStatus,
    WebhookEnvelope,
)
from control_plane.repository import Repository
from control_plane.service import ControlPlaneService

router = APIRouter()


def service(request: Request) -> ControlPlaneService:
    return cast(ControlPlaneService, request.app.state.service)


def repository(request: Request) -> Repository:
    return cast(Repository, request.app.state.repository)


def visible_run(run_id: UUID, request: Request, actor: Actor | None) -> RunRecord:
    run = repository(request).get_run(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    if not run.is_public_demo and (actor is None or actor.tenant_id != run.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return run


@router.get("/healthz", include_in_schema=False)
@router.get("/v1/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "agent-control-api"}


@router.post("/v1/cases", status_code=status.HTTP_201_CREATED)
async def create_case(
    payload: CaseRequest,
    request: Request,
    actor: Annotated[Actor, Depends(require_role("operator"))],
) -> Any:
    try:
        return service(request).create_case(payload, actor)
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.post("/v1/cases/{case_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    case_id: UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_role("operator"))],
) -> Any:
    try:
        return await service(request).start_run(case_id, actor)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found") from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.get("/v1/runs")
async def list_runs(
    request: Request,
    actor: Annotated[Actor | None, Depends(optional_actor)],
) -> list[RunRecord]:
    runs = repository(request).list_runs(public_only=actor is None)
    if actor:
        runs = [run for run in runs if run.is_public_demo or run.tenant_id == actor.tenant_id]
    return runs


@router.get("/v1/runs/{run_id}")
async def get_run(
    run_id: UUID,
    request: Request,
    actor: Annotated[Actor | None, Depends(optional_actor)],
) -> RunRecord:
    return visible_run(run_id, request, actor)


@router.get("/v1/runs/{run_id}/events")
async def get_events(
    run_id: UUID,
    request: Request,
    actor: Annotated[Actor | None, Depends(optional_actor)],
) -> EventSourceResponse:
    visible_run(run_id, request, actor)
    repo = repository(request)

    async def stream() -> AsyncIterator[dict[str, str]]:
        sent: set[str] = set()
        idle = 0
        while idle < 30:
            events = repo.events(run_id)
            new_events = [event for event in events if str(event.event_id) not in sent]
            for event in new_events:
                sent.add(str(event.event_id))
                yield {
                    "id": str(event.event_id),
                    "event": event.event_type,
                    "data": event.model_dump_json(),
                }
            run = repo.get_run(run_id)
            if run and run.status in {RunStatus.COMPLETED, RunStatus.BLOCKED, RunStatus.FAILED}:
                yield {"event": "end", "data": json.dumps({"status": run.status})}
                return
            idle = 0 if new_events else idle + 1
            if not new_events:
                yield {"event": "heartbeat", "data": "{}"}
            await asyncio.sleep(1)

    return EventSourceResponse(stream())


@router.post("/v1/runs/{run_id}/approvals")
async def approve_run(
    run_id: UUID,
    payload: ApprovalRequest,
    request: Request,
    actor: Annotated[Actor, Depends(require_role("approver"))],
) -> RunRecord:
    decision = ApprovalDecision(
        approved=payload.approved,
        actor_id=actor.subject,
        comment=payload.comment,
        proposed_action_hash=payload.proposed_action_hash,
    )
    try:
        return await service(request).approve(run_id, decision, actor)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found") from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/v1/runs/{run_id}/replay", status_code=status.HTTP_202_ACCEPTED)
async def replay_run(
    run_id: UUID,
    payload: ReplayRequest,
    request: Request,
    actor: Annotated[Actor, Depends(require_role("operator"))],
) -> RunRecord:
    try:
        return await service(request).replay(run_id, payload, actor)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found") from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.post("/v1/webhooks/cases", status_code=status.HTTP_202_ACCEPTED)
async def webhook_case(
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    signature: Annotated[str, Header(alias="X-Signature")],
) -> JSONResponse:
    raw = await request.body()
    expected = hmac.new(
        request.app.state.settings.webhook_secret.encode(), raw, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature.removeprefix("sha256="), expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid webhook signature")
    payload = WebhookEnvelope.model_validate_json(raw)
    if payload.event_id != idempotency_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "event and idempotency keys differ")
    if not repository(request).claim_webhook(idempotency_key):
        return JSONResponse({"status": "duplicate", "event_id": payload.event_id}, status_code=200)
    actor = Actor("signed-case-webhook", payload.case.tenant_id, frozenset({"operator"}))
    case = service(request).create_case(payload.case, actor)
    run = await service(request).start_run(case.case_id, actor)
    return JSONResponse(
        {"status": "accepted", "event_id": payload.event_id, "run_id": str(run.run_id)},
        status_code=202,
    )


@router.get("/v1/evals/{eval_id}")
async def get_eval(eval_id: str) -> Any:
    if not eval_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid evaluation id")
    path = Path("evals/results") / f"{eval_id}.json"
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "evaluation not found")
    return json.loads(path.read_text())


@router.get("/v1/metrics/summary")
async def metrics_summary(request: Request) -> MetricsSummary:
    runs = repository(request).list_runs(public_only=True)
    if not runs:
        return MetricsSummary(
            total_runs=0,
            success_rate=0,
            error_rate=0,
            p95_latency_ms=0,
            average_model_cost_usd=0,
            recovery_rate=0,
            unsafe_writes=0,
            duplicate_side_effects=0,
            source="seeded-demo",
        )
    latencies = sorted(float(run.state.get("latency_ms", 0)) for run in runs)
    p95_index = min(len(latencies) - 1, max(0, round(0.95 * len(latencies) - 1)))
    successes = sum(run.status == RunStatus.COMPLETED for run in runs)
    recovered = sum(
        bool(run.state.get("retry_count")) and run.status == RunStatus.COMPLETED for run in runs
    )
    recovery_candidates = sum(bool(run.state.get("retry_count")) for run in runs)
    return MetricsSummary(
        total_runs=len(runs),
        success_rate=successes / len(runs),
        error_rate=sum(run.status == RunStatus.FAILED for run in runs) / len(runs),
        p95_latency_ms=latencies[p95_index],
        average_model_cost_usd=statistics.fmean(
            float(run.state.get("model_cost_usd", 0)) for run in runs
        ),
        recovery_rate=recovered / recovery_candidates if recovery_candidates else 1,
        unsafe_writes=0,
        duplicate_side_effects=0,
        source="seeded-demo",
    )
