from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import AuthContext, require_auth_context
from src.core.db import get_db_session
from src.core.repositories.trading_profiles import TradingProfileRepository
from src.schemas.profile import (
    MasterSwitchUpdateRequest,
    TradingProfileResponse,
    TradingProfileUpsertRequest,
)

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/trading", response_model=TradingProfileResponse)
async def get_trading_profile(
    auth: AuthContext = Depends(require_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> TradingProfileResponse:
    repository = TradingProfileRepository(session)
    profile = await repository.get_by_user_id(auth.user_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trading profile not found",
        )

    return TradingProfileResponse(
        max_daily_loss=profile.max_daily_loss,
        max_orders=profile.max_orders,
        master_switch_enabled=profile.master_switch_enabled,
    )


@router.put("/trading", response_model=TradingProfileResponse)
async def upsert_trading_profile(
    payload: TradingProfileUpsertRequest,
    auth: AuthContext = Depends(require_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> TradingProfileResponse:
    repository = TradingProfileRepository(session)
    existing = await repository.get_by_user_id(auth.user_id)

    if existing is None:
        profile = await repository.create(
            user_id=auth.user_id,
            max_daily_loss=payload.max_daily_loss,
            max_orders=payload.max_orders,
            master_switch_enabled=payload.master_switch_enabled,
        )
    else:
        profile = await repository.update(
            existing.id,
            max_daily_loss=payload.max_daily_loss,
            max_orders=payload.max_orders,
            master_switch_enabled=payload.master_switch_enabled,
        )
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update trading profile",
            )

    await session.commit()
    return TradingProfileResponse(
        max_daily_loss=profile.max_daily_loss,
        max_orders=profile.max_orders,
        master_switch_enabled=profile.master_switch_enabled,
    )


@router.patch("/trading/master-switch", response_model=TradingProfileResponse)
async def update_master_switch(
    payload: MasterSwitchUpdateRequest,
    auth: AuthContext = Depends(require_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> TradingProfileResponse:
    repository = TradingProfileRepository(session)
    profile = await repository.get_by_user_id(auth.user_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trading profile not found",
        )

    updated = await repository.update(profile.id, master_switch_enabled=payload.enabled)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update master switch",
        )

    await session.commit()
    return TradingProfileResponse(
        max_daily_loss=updated.max_daily_loss,
        max_orders=updated.max_orders,
        master_switch_enabled=updated.master_switch_enabled,
    )
