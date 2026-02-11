from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import UUID

import requests
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.context import set_current_tenant_id
from src.core.db import get_db_session
from src.models.tenant import Tenant

bearer_scheme = HTTPBearer(auto_error=True)


@dataclass(slots=True)
class AuthContext:
    tenant_id: UUID
    subject: str
    org_id: str


class JwksCache:
    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl_seconds = ttl_seconds
        self._jwks: dict | None = None
        self._fetched_at = 0.0

    def get(self, url: str) -> dict:
        now = time.time()
        if self._jwks is None or (now - self._fetched_at) > self.ttl_seconds:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            self._jwks = response.json()
            self._fetched_at = now
        return self._jwks


jwks_cache = JwksCache()


def _get_signing_key(token: str) -> dict:
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication header",
        ) from exc

    kid = unverified_header.get("kid")
    if not kid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT is missing key id",
        )

    jwks = jwks_cache.get(settings.clerk_jwks_url)
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No matching signing key found",
    )


def _decode_clerk_jwt(token: str) -> dict:
    key = _get_signing_key(token)

    options = {"verify_aud": bool(settings.clerk_audience)}
    try:
        return jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=settings.clerk_issuer,
            audience=settings.clerk_audience or None,
            options=options,
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


async def require_auth_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> AuthContext:
    claims = _decode_clerk_jwt(credentials.credentials)

    org_id = claims.get("org_id")
    subject = claims.get("sub")

    if not org_id or not subject:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token is missing required claims",
        )

    tenant = await session.scalar(
        select(Tenant).where(Tenant.clerk_org_id == org_id)
    )
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization is not provisioned",
        )

    request.state.tenant_id = tenant.id
    request.state.auth_claims = claims
    request.state.user_subject = subject
    set_current_tenant_id(tenant.id)

    return AuthContext(tenant_id=tenant.id, subject=subject, org_id=org_id)
