from __future__ import annotations

from pydantic import BaseModel, Field


class KiteCredentialUpsertRequest(BaseModel):
    api_key: str = Field(min_length=4, max_length=255)
    api_secret: str = Field(min_length=4, max_length=255)
    totp_secret: str = Field(min_length=8, max_length=255)


class KiteCredentialStatusResponse(BaseModel):
    linked: bool
    updated_at: str | None = None


class KiteConnectionCheckRequest(BaseModel):
    request_token: str = Field(min_length=8, max_length=1024)


class KiteConnectionCheckResponse(BaseModel):
    success: bool
    message: str
    kite_user_id: str | None = None
