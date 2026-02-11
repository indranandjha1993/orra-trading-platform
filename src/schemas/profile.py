from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class TradingProfileUpsertRequest(BaseModel):
    max_daily_loss: Decimal = Field(gt=0)
    max_orders: int = Field(gt=0, le=500)
    master_switch_enabled: bool = False


class TradingProfileResponse(BaseModel):
    max_daily_loss: Decimal
    max_orders: int
    master_switch_enabled: bool


class MasterSwitchUpdateRequest(BaseModel):
    enabled: bool
