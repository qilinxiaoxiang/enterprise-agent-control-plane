from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane.app_factory import create_app
from control_plane.config import Settings
from control_plane.repository import MemoryRepository
from control_plane.tool_gateway import SyntheticToolGateway


@pytest.fixture
def repository() -> MemoryRepository:
    return MemoryRepository()


@pytest.fixture
def gateway() -> SyntheticToolGateway:
    return SyntheticToolGateway()


@pytest.fixture
def client(
    tmp_path: Path,
    repository: MemoryRepository,
    gateway: SyntheticToolGateway,
) -> TestClient:
    app = create_app(
        Settings(app_env="test", frontend_dir=tmp_path / "missing"),
        repository=repository,
        tools=gateway,
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def case_payload() -> dict[str, object]:
    return {
        "tenant_id": "northstar-bank",
        "customer_id": "cust-test-001",
        "transfer_id": "txn-test-001",
        "anomaly_type": "profile_mismatch",
        "requested_action": "release_transfer",
        "amount": 12500,
        "currency": "USD",
        "notes": "Synthetic API contract test.",
    }
