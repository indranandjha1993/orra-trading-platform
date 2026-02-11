from __future__ import annotations

from datetime import datetime, timezone

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, status
from kiteconnect import KiteConnect
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import AuthContext, require_auth_context
from src.core.config import settings
from src.core.db import get_db_session
from src.core.repositories.kite_credentials import KiteCredentialRepository
from src.core.security.crypto import EncryptionError
from src.core.security.dependencies import get_security_cipher
from src.schemas.account import (
    KiteConnectionCheckRequest,
    KiteConnectionCheckResponse,
    KiteCredentialStatusResponse,
    KiteCredentialUpsertRequest,
)

router = APIRouter(prefix="/account", tags=["account"])


@router.put("/kite-credentials", response_model=KiteCredentialStatusResponse)
async def upsert_kite_credentials(
    payload: KiteCredentialUpsertRequest,
    auth: AuthContext = Depends(require_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> KiteCredentialStatusResponse:
    cipher = get_security_cipher()
    repository = KiteCredentialRepository(session)
    existing = await repository.get_by_user_id(auth.user_id)

    encrypted_values = {
        "api_key_encrypted": cipher.encrypt(payload.api_key),
        "api_secret_encrypted": cipher.encrypt(payload.api_secret),
        "totp_secret_encrypted": cipher.encrypt(payload.totp_secret),
    }

    if existing is None:
        credential = await repository.create(user_id=auth.user_id, **encrypted_values)
    else:
        credential = await repository.update(existing.id, **encrypted_values)
        if credential is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update Kite credentials",
            )

    await session.commit()
    return KiteCredentialStatusResponse(
        linked=True,
        updated_at=credential.updated_at.astimezone(timezone.utc).isoformat(),
    )


@router.get("/kite-credentials/status", response_model=KiteCredentialStatusResponse)
async def kite_credentials_status(
    auth: AuthContext = Depends(require_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> KiteCredentialStatusResponse:
    repository = KiteCredentialRepository(session)
    credential = await repository.get_by_user_id(auth.user_id)
    if credential is None:
        return KiteCredentialStatusResponse(linked=False)

    return KiteCredentialStatusResponse(
        linked=True,
        updated_at=credential.updated_at.astimezone(timezone.utc).isoformat(),
    )


@router.post("/kite/check-connection", response_model=KiteConnectionCheckResponse)
async def check_kite_connection(
    payload: KiteConnectionCheckRequest,
    auth: AuthContext = Depends(require_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> KiteConnectionCheckResponse:
    repository = KiteCredentialRepository(session)
    credential = await repository.get_by_user_id(auth.user_id)
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kite credentials not found",
        )

    cipher = get_security_cipher()
    try:
        api_key = cipher.decrypt(credential.api_key_encrypted)
        api_secret = cipher.decrypt(credential.api_secret_encrypted)
        _ = cipher.decrypt(credential.totp_secret_encrypted)
    except EncryptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored credentials could not be decrypted",
        ) from exc

    kite = KiteConnect(api_key=api_key)
    try:
        session_data = kite.generate_session(payload.request_token, api_secret=api_secret)
        access_token = session_data.get("access_token")
        if not access_token:
            raise ValueError("Kite access token is missing from response")

        redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis_client.set(
                f"kite:access_token:{auth.tenant_id}",
                access_token,
                ex=settings.auth_token_ttl_seconds,
            )
            await redis_client.set(
                f"kite:connection_status:{auth.tenant_id}",
                "connected",
                ex=settings.auth_token_ttl_seconds,
            )
        finally:
            await redis_client.aclose()

        return KiteConnectionCheckResponse(
            success=True,
            message="Kite connection successful",
            kite_user_id=session_data.get("user_id"),
        )
    except Exception as exc:
        return KiteConnectionCheckResponse(
            success=False,
            message=f"Kite connection failed: {exc}",
        )
