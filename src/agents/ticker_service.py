from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from uuid import UUID

from fastapi import FastAPI
from kiteconnect import KiteTicker
from redis import Redis
from sqlalchemy import select

from src.agents.health import AgentHealth
from src.core.config import settings
from src.core.db import AsyncSessionLocal
from src.core.security.dependencies import get_security_cipher
from src.models.kite_credential import KiteCredential
from src.models.tenant import Tenant

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TenantTickerConfig:
    tenant_id: UUID
    api_key_encrypted: str


class TenantTickerWorker:
    def __init__(
        self,
        tenant_id: UUID,
        api_key: str,
        redis_client: Redis,
        instrument_tokens: list[int],
        health: AgentHealth,
    ) -> None:
        self.tenant_id = tenant_id
        self.api_key = api_key
        self.redis_client = redis_client
        self.instrument_tokens = instrument_tokens
        self.health = health

        self._loop = asyncio.get_running_loop()
        self._should_run = True
        self._disconnect_event = asyncio.Event()
        self._kws: KiteTicker | None = None

    async def stop(self) -> None:
        self._should_run = False
        if self._kws is not None:
            try:
                self._kws.close()
            except Exception:  # pragma: no cover - network callback path
                logger.exception("Ticker close failed for tenant=%s", self.tenant_id)
        self._disconnect_event.set()

    async def run(self) -> None:
        delay = settings.ticker_reconnect_initial_delay_seconds
        while self._should_run:
            access_token = await asyncio.to_thread(
                self.redis_client.get, f"kite:access_token:{self.tenant_id}"
            )
            if not access_token:
                logger.warning("No access token in redis for tenant=%s", self.tenant_id)
                await asyncio.sleep(delay)
                delay = min(delay * 2, settings.ticker_reconnect_max_delay_seconds)
                continue

            self._disconnect_event = asyncio.Event()
            self._kws = KiteTicker(api_key=self.api_key, access_token=access_token)
            self._configure_callbacks(self._kws)

            try:
                self._kws.connect(threaded=True, reconnect=False)
                await self._disconnect_event.wait()
            except Exception:  # pragma: no cover - network callback path
                logger.exception("Ticker worker crashed for tenant=%s", self.tenant_id)
            finally:
                if self._kws is not None:
                    try:
                        self._kws.close()
                    except Exception:
                        logger.exception("Ticker close failed for tenant=%s", self.tenant_id)
                    self._kws = None

            if not self._should_run:
                break

            await asyncio.sleep(delay)
            delay = min(delay * 2, settings.ticker_reconnect_max_delay_seconds)

    def _configure_callbacks(self, kws: KiteTicker) -> None:
        def on_connect(ws: KiteTicker, response: dict) -> None:
            logger.info("Ticker connected for tenant=%s", self.tenant_id)
            ws.subscribe(self.instrument_tokens)
            ws.set_mode(ws.MODE_FULL, self.instrument_tokens)
            self.health.metrics["active_connections"] = self.health.metrics.get("active_connections", 0) + 1

        def on_ticks(ws: KiteTicker, ticks: list[dict]) -> None:
            for tick in ticks:
                instrument_token = tick.get("instrument_token")
                if instrument_token is None:
                    continue
                channel = f"ticker:{self.tenant_id}:{instrument_token}"
                payload = json.dumps(tick, default=str)
                self.redis_client.publish(channel, payload)
                self.health.metrics["ticks_published"] = self.health.metrics.get("ticks_published", 0) + 1

        def on_close(ws: KiteTicker, code: int, reason: str) -> None:
            logger.warning(
                "Ticker closed tenant=%s code=%s reason=%s", self.tenant_id, code, reason
            )
            self.health.metrics["active_connections"] = max(
                0, self.health.metrics.get("active_connections", 0) - 1
            )
            self._loop.call_soon_threadsafe(self._disconnect_event.set)

        def on_error(ws: KiteTicker, code: int, reason: str) -> None:
            logger.error(
                "Ticker error tenant=%s code=%s reason=%s", self.tenant_id, code, reason
            )
            self.health.last_error = f"{code}:{reason}"
            self._loop.call_soon_threadsafe(self._disconnect_event.set)

        kws.on_connect = on_connect
        kws.on_ticks = on_ticks
        kws.on_close = on_close
        kws.on_error = on_error


class TickerAgent:
    def __init__(self) -> None:
        self.health = AgentHealth(name="ticker-agent", ready=True)
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._workers: list[TenantTickerWorker] = []
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True)

    async def stop(self) -> None:
        self._stop_event.set()
        for worker in self._workers:
            await worker.stop()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await asyncio.to_thread(self._redis.close)

    async def run(self) -> None:
        if not settings.ticker_instrument_tokens():
            err = ValueError("No ticker instruments configured in TICKER_INSTRUMENT_TOKENS_CSV")
            self.health.mark_error(err)
            logger.error(str(err))
            return

        retry_delay = settings.ticker_reconnect_initial_delay_seconds
        while not self._stop_event.is_set():
            self.health.mark_run()
            try:
                await self._run_workers_once()
                self.health.mark_success()
                retry_delay = settings.ticker_reconnect_initial_delay_seconds
                await asyncio.wait_for(self._stop_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                continue
            except Exception as exc:  # pragma: no cover - operational path
                self.health.mark_error(exc)
                logger.exception("Ticker agent cycle failed")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, settings.ticker_reconnect_max_delay_seconds)

    async def _run_workers_once(self) -> None:
        configs = await self._fetch_tenant_configs()
        self.health.metrics["tenants_seen"] = len(configs)

        # Start workers only once.
        if self._tasks:
            return

        cipher = get_security_cipher()
        instruments = settings.ticker_instrument_tokens()

        for config in configs:
            api_key = cipher.decrypt(config.api_key_encrypted)
            worker = TenantTickerWorker(
                tenant_id=config.tenant_id,
                api_key=api_key,
                redis_client=self._redis,
                instrument_tokens=instruments,
                health=self.health,
            )
            self._workers.append(worker)
            self._tasks.append(asyncio.create_task(worker.run()))

    async def _fetch_tenant_configs(self) -> list[TenantTickerConfig]:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(Tenant.id, KiteCredential.api_key_encrypted)
                .join(KiteCredential, KiteCredential.tenant_id == Tenant.id)
                .order_by(Tenant.created_at)
            )
            rows = (await session.execute(stmt)).all()

        return [
            TenantTickerConfig(tenant_id=row.id, api_key_encrypted=row.api_key_encrypted)
            for row in rows
        ]


ticker_agent = TickerAgent()
app = FastAPI(title="Orra Ticker Agent")


@app.on_event("startup")
async def startup_event() -> None:
    logging.basicConfig(level=logging.INFO)
    app.state.task = asyncio.create_task(ticker_agent.run())


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await ticker_agent.stop()
    task: asyncio.Task[None] = app.state.task
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@app.get("/health", tags=["system"])
async def health() -> dict[str, object]:
    return ticker_agent.health.payload()


@app.get("/ready", tags=["system"])
async def ready() -> dict[str, bool]:
    return {"ready": ticker_agent.health.ready}
