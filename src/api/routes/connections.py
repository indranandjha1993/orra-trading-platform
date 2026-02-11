from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from kiteconnect import KiteConnect
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import AuthContext, require_auth_context
from src.core.db import get_db_session
from src.core.repositories.kite_credentials import KiteCredentialRepository
from src.core.security.crypto import EncryptionError
from src.core.security.dependencies import get_security_cipher
from src.schemas.security import KiteConnectionTestRequest, KiteConnectionTestResponse

router = APIRouter(prefix="/connections", tags=["connections"])


@router.post("/kite/test", response_model=KiteConnectionTestResponse)
async def test_kite_connection(
    payload: KiteConnectionTestRequest,
    auth: AuthContext = Depends(require_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> KiteConnectionTestResponse:
    credentials_repo = KiteCredentialRepository(session)
    credential = await credentials_repo.get_by_user_id(payload.user_id)
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kite credentials not found for this tenant/user",
        )
    if credential.tenant_id != auth.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Credential does not belong to the authenticated tenant",
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
        kite.set_access_token(access_token)
        profile = kite.profile()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Kite connection test failed: {exc}",
        ) from exc

    return KiteConnectionTestResponse(
        connected=True,
        kite_user_id=session_data.get("user_id"),
        user_name=profile.get("user_name"),
    )
