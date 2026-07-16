from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from control_plane.domain import ProposedAction
from control_plane.workflow import action_hash


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_public_can_inspect_only_precomputed_synthetic_runs(client: TestClient) -> None:
    public = client.get("/v1/runs")
    assert public.status_code == 200
    assert len(public.json()) == 3
    assert all(run["is_public_demo"] for run in public.json())


def test_operator_create_approver_resume_api_contract(
    client: TestClient, case_payload: dict[str, object]
) -> None:
    created = client.post("/v1/cases", json=case_payload, headers=auth("dev-operator"))
    assert created.status_code == 201
    run = client.post(f"/v1/cases/{created.json()['case_id']}/runs", headers=auth("dev-operator"))
    assert run.status_code == 202
    assert run.json()["status"] == "awaiting_approval"

    action = ProposedAction.model_validate(run.json()["state"]["proposed_action"])
    operator_denied = client.post(
        f"/v1/runs/{run.json()['run_id']}/approvals",
        json={
            "approved": True,
            "comment": "wrong role",
            "proposed_action_hash": action_hash(action),
        },
        headers=auth("dev-operator"),
    )
    assert operator_denied.status_code == 403

    approved = client.post(
        f"/v1/runs/{run.json()['run_id']}/approvals",
        json={"approved": True, "comment": "verified", "proposed_action_hash": action_hash(action)},
        headers=auth("dev-approver"),
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "completed"


def test_cross_tenant_and_anonymous_mutations_are_denied(
    client: TestClient, case_payload: dict[str, object]
) -> None:
    assert client.post("/v1/cases", json=case_payload).status_code == 401
    other = dict(case_payload, tenant_id="other-bank")
    assert client.post("/v1/cases", json=other, headers=auth("dev-operator")).status_code == 403


def test_signed_webhook_is_idempotent(client: TestClient, case_payload: dict[str, object]) -> None:
    body = json.dumps(
        {"event_id": "event-webhook-0001", "case": case_payload}, separators=(",", ":")
    ).encode()
    signature = hmac.new(b"local-synthetic-secret", body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": "event-webhook-0001",
        "X-Signature": f"sha256={signature}",
    }
    first = client.post("/v1/webhooks/cases", content=body, headers=headers)
    second = client.post("/v1/webhooks/cases", content=body, headers=headers)
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"


def test_webhook_rejects_bad_signature(client: TestClient, case_payload: dict[str, object]) -> None:
    response = client.post(
        "/v1/webhooks/cases",
        json={"event_id": "event-webhook-0002", "case": case_payload},
        headers={"Idempotency-Key": "event-webhook-0002", "X-Signature": "sha256=bad"},
    )
    assert response.status_code == 401
