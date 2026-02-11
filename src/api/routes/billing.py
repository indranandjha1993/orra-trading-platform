from __future__ import annotations

from fastapi import APIRouter, Depends

from src.core.billing import (
    TierEntitlements,
    enforce_daily_trade_limit,
    enforce_strategy_limit,
    get_current_entitlements,
    require_pro_tier,
)
from src.schemas.billing import EntitlementResponse

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/entitlements", response_model=EntitlementResponse)
async def get_entitlements(
    entitlements: TierEntitlements = Depends(get_current_entitlements),
) -> EntitlementResponse:
    return EntitlementResponse(
        tier=entitlements.tier,
        max_strategies=entitlements.max_strategies,
        daily_trade_limit=entitlements.daily_trade_limit,
        priority_execution=entitlements.priority_execution,
    )


@router.post("/guards/strategy")
async def strategy_guard(
    _: TierEntitlements = Depends(enforce_strategy_limit),
) -> dict[str, bool]:
    return {"allowed": True}


@router.post("/guards/trade")
async def trade_guard(
    _: TierEntitlements = Depends(enforce_daily_trade_limit),
) -> dict[str, bool]:
    return {"allowed": True}


@router.post("/guards/priority")
async def priority_guard(
    _: TierEntitlements = Depends(require_pro_tier),
) -> dict[str, bool]:
    return {"allowed": True}
