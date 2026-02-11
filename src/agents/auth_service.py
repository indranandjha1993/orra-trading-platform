from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pyotp
import redis.asyncio as redis
from fastapi import FastAPI
from kiteconnect import KiteConnect
from playwright.async_api import Browser, async_playwright
from sqlalchemy import select

from src.agents.health import AgentHealth
from src.core.config import settings
from src.core.db import AsyncSessionLocal
from src.core.security.dependencies import get_security_cipher
from src.models.kite_credential import KiteCredential
from src.models.tenant import Tenant

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TenantAuthRecord:
    tenant_id: UUID
    user_id: UUID
    api_key_encrypted: str
    api_secret_encrypted: str
    totp_secret_encrypted: str


class AuthAgent:
    def __init__(self) -> None:
        self.health = AgentHealth(name="auth-agent", ready=True)
        self._stop_event = asyncio.Event()

    async def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        retry_delay = 1
        while not self._stop_event.is_set():
            self.health.mark_run()
            try:
                await self._refresh_all_tenant_tokens()
                self.health.mark_success()
                retry_delay = 1
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=settings.auth_refresh_interval_seconds
                )
            except asyncio.TimeoutError:
                continue
            except Exception as exc:  # pragma: no cover - operational path
                self.health.mark_error(exc)
                logger.exception("Auth agent cycle failed")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, settings.auth_max_retry_delay_seconds)

    async def _refresh_all_tenant_tokens(self) -> None:
        records = await self._fetch_tenant_records()
        refreshed = 0
        redis_client = redis.from_url(settings.redis_url, decode_responses=True)

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=settings.playwright_headless)
            try:
                for record in records:
                    if self._stop_event.is_set():
                        break
                    try:
                        await self._refresh_single_tenant_token(browser, redis_client, record)
                        refreshed += 1
                    except Exception as exc:
                        logger.exception("Tenant token refresh failed for tenant=%s", record.tenant_id)
                        await self._emit_auth_failure_event(redis_client, record, exc)
            finally:
                await browser.close()
                await redis_client.aclose()

        self.health.metrics["tenants_seen"] = len(records)
        self.health.metrics["tenants_refreshed"] = refreshed

    async def _fetch_tenant_records(self) -> list[TenantAuthRecord]:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(
                    Tenant.id,
                    KiteCredential.user_id,
                    KiteCredential.api_key_encrypted,
                    KiteCredential.api_secret_encrypted,
                    KiteCredential.totp_secret_encrypted,
                )
                .join(KiteCredential, KiteCredential.tenant_id == Tenant.id)
                .where(Tenant.is_active.is_(True))
                .order_by(Tenant.created_at)
            )
            rows = (await session.execute(stmt)).all()

        return [
            TenantAuthRecord(
                tenant_id=row.id,
                user_id=row.user_id,
                api_key_encrypted=row.api_key_encrypted,
                api_secret_encrypted=row.api_secret_encrypted,
                totp_secret_encrypted=row.totp_secret_encrypted,
            )
            for row in rows
        ]

    async def _emit_auth_failure_event(
        self,
        redis_client: redis.Redis,
        record: TenantAuthRecord,
        error: Exception,
    ) -> None:
        await redis_client.xadd(
            settings.auth_error_stream_name,
            {
                "event_type": "auth_2fa_failed",
                "tenant_id": str(record.tenant_id),
                "user_id": str(record.user_id),
                "severity": "urgent",
                "error": str(error),
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _refresh_single_tenant_token(
        self,
        browser: Browser,
        redis_client: redis.Redis,
        record: TenantAuthRecord,
    ) -> None:
        cipher = get_security_cipher()
        api_key = cipher.decrypt(record.api_key_encrypted)
        api_secret = cipher.decrypt(record.api_secret_encrypted)
        totp_secret = cipher.decrypt(record.totp_secret_encrypted)

        request_token = await self._perform_zerodha_login(
            browser=browser,
            tenant_id=record.tenant_id,
            api_key=api_key,
            totp_secret=totp_secret,
        )

        kite = KiteConnect(api_key=api_key)
        session_data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = session_data.get("access_token")
        if not access_token:
            raise ValueError(f"Missing access token for tenant {record.tenant_id}")

        key = f"kite:access_token:{record.tenant_id}"
        await redis_client.set(key, access_token, ex=settings.auth_token_ttl_seconds)
        await redis_client.publish(
            f"auth:token_refreshed:{record.tenant_id}",
            datetime.now(timezone.utc).isoformat(),
        )

    async def _perform_zerodha_login(
        self,
        browser: Browser,
        tenant_id: UUID,
        api_key: str,
        totp_secret: str,
    ) -> str:
        user_map = settings.tenant_zerodha_users()
        password_map = settings.tenant_zerodha_passwords()

        tenant_key = str(tenant_id)
        user_id = user_map.get(tenant_key)
        password = password_map.get(tenant_key)
        if not user_id or not password:
            raise ValueError(
                f"Missing Zerodha credentials maps for tenant={tenant_id}. "
                "Set ZERODHA_USER_ID_MAP_JSON and ZERODHA_PASSWORD_MAP_JSON."
            )

        page = await browser.new_page()
        try:
            kite = KiteConnect(api_key=api_key)
            await page.goto(kite.login_url(), wait_until="networkidle")

            await page.fill(settings.zerodha_user_selector, user_id)
            await page.fill(settings.zerodha_password_selector, password)
            await page.click(settings.zerodha_submit_selector)

            totp = pyotp.TOTP(totp_secret).now()
            await page.wait_for_timeout(1000)
            await page.fill(settings.zerodha_totp_selector, totp)
            await page.click(settings.zerodha_submit_selector)

            await page.wait_for_url("**request_token=**", timeout=45_000)
            current_url = page.url
            token = parse_qs(urlparse(current_url).query).get("request_token", [None])[0]
            if not token:
                raise ValueError(f"request_token not found after login for tenant={tenant_id}")
            return token
        finally:
            await page.close()


auth_agent = AuthAgent()
app = FastAPI(title="Orra Auth Agent")


@app.on_event("startup")
async def startup_event() -> None:
    logging.basicConfig(level=logging.INFO)
    app.state.task = asyncio.create_task(auth_agent.run())


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await auth_agent.stop()
    task: asyncio.Task[None] = app.state.task
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@app.get("/health", tags=["system"])
async def health() -> dict[str, object]:
    return auth_agent.health.payload()


@app.get("/ready", tags=["system"])
async def ready() -> dict[str, bool]:
    return {"ready": auth_agent.health.ready}
