from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class KiteConnectionTestRequest(BaseModel):
    user_id: UUID
    request_token: str = Field(min_length=8, max_length=1024)


class KiteConnectionTestResponse(BaseModel):
    connected: bool
    kite_user_id: str | None = None
    user_name: str | None = None
