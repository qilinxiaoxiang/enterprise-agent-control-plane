from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from control_plane.config import Settings, get_settings

Role = Literal["viewer", "operator", "approver"]


@dataclass(frozen=True)
class Actor:
    subject: str
    tenant_id: str
    roles: frozenset[Role]


bearer = HTTPBearer(auto_error=False)


def _dev_actor(token: str) -> Actor:
    actors = {
        "dev-viewer": Actor("local-viewer", "northstar-bank", frozenset({"viewer"})),
        "dev-operator": Actor(
            "local-operator", "northstar-bank", frozenset({"viewer", "operator"})
        ),
        "dev-approver": Actor(
            "local-approver", "northstar-bank", frozenset({"viewer", "approver"})
        ),
    }
    if token not in actors:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid development token")
    return actors[token]


def _firebase_actor(token: str, settings: Settings) -> Actor:
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        claims = id_token.verify_firebase_token(  # type: ignore[no-untyped-call]
            token, google_requests.Request()
        )
    except Exception as exc:  # verification failures intentionally collapse to 401
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid Firebase token") from exc
    if not claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid Firebase token")
    roles = frozenset(role for role in ("viewer", "operator", "approver") if claims.get(role))
    tenant_id = str(claims.get("tenant_id", ""))
    if not tenant_id or not roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing role or tenant claims")
    return Actor(str(claims["sub"]), tenant_id, roles)  # type: ignore[arg-type]


def optional_actor(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Actor | None:
    if credentials is None:
        return None
    if settings.auth_mode == "dev":
        return _dev_actor(credentials.credentials)
    return _firebase_actor(credentials.credentials, settings)


def require_role(role: Role) -> Callable[[Actor | None], Actor]:
    def dependency(actor: Annotated[Actor | None, Depends(optional_actor)]) -> Actor:
        if actor is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
        if role not in actor.roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"{role} role required")
        return actor

    return dependency
