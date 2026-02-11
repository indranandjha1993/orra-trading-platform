from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

import redis.asyncio as redis
import requests
from fastapi import FastAPI
from sqlalchemy import select

from src.agents.health import AgentHealth
from src.core.config import settings
from src.core.db import AsyncSessionLocal
from src.models.notification_preference import NotificationPreference
from src.models.tenant import Tenant

logger = logging.getLogger(__name__)

Channel = str


@dataclass(slots=True)
class NotificationTarget:
    channel: Channel
    destination: str


class NotificationDispatcher:
    def __init__(self) -> None:
        self.channel_webhooks: dict[str, str] = {
            "telegram": settings.n8n_telegram_webhook_url,
            "whatsapp": settings.n8n_whatsapp_webhook_url,
            "email": settings.n8n_email_webhook_url,
        }

    async def dispatch(
        self,
        *,
        channel: str,
        payload: dict[str, object],
        urgent: bool = False,
    ) -> None:
        webhook = settings.n8n_urgent_webhook_url if urgent else self.channel_webhooks.get(channel, "")
        if not webhook:
            raise ValueError(f"Webhook is not configured for channel={channel} urgent={urgent}")

        def _post() -> None:
            # n8n workflow pattern: single webhook entry with event + routing metadata.
            response = requests.post(webhook, json=payload, timeout=10)
            response.raise_for_status()

        await asyncio.to_thread(_post)


class NotificationAgent:
    def __init__(self) -> None:
        self.health = AgentHealth(name="notification-agent", ready=True)
        self._stop_event = asyncio.Event()
        self._redis = redis.from_url(settings.redis_url, decode_responses=True)
        self._dispatcher = NotificationDispatcher()

    async def stop(self) -> None:
        self._stop_event.set()
        await self._redis.aclose()

    async def run(self) -> None:
        await self._ensure_consumer_groups()

        retry_delay = 1
        while not self._stop_event.is_set():
            self.health.mark_run()
            try:
                messages = await self._redis.xreadgroup(
                    groupname=settings.notification_consumer_group,
                    consumername=settings.notification_consumer_name,
                    streams={
                        settings.execution_results_stream_name: ">",
                        settings.auth_error_stream_name: ">",
                    },
                    count=100,
                    block=settings.notification_stream_block_ms,
                )

                if not messages:
                    continue

                for stream_name, entries in messages:
                    for message_id, fields in entries:
                        await self._process_event(stream_name, message_id, fields)

                self.health.mark_success()
                retry_delay = 1
            except Exception as exc:  # pragma: no cover - operational path
                self.health.mark_error(exc)
                logger.exception("Notification agent stream loop failed")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 120)

    async def _ensure_consumer_groups(self) -> None:
        for stream in (settings.execution_results_stream_name, settings.auth_error_stream_name):
            try:
                await self._redis.xgroup_create(
                    name=stream,
                    groupname=settings.notification_consumer_group,
                    id="0",
                    mkstream=True,
                )
            except Exception as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

    async def _process_event(self, stream_name: str, message_id: str, fields: dict[str, str]) -> None:
        try:
            if stream_name == settings.execution_results_stream_name:
                await self._handle_execution_result(fields)
            elif stream_name == settings.auth_error_stream_name:
                await self._handle_auth_error(fields)
            self.health.metrics["events_processed"] = self.health.metrics.get("events_processed", 0) + 1
            await self._redis.xack(stream_name, settings.notification_consumer_group, message_id)
        except Exception as exc:
            self.health.metrics["events_failed"] = self.health.metrics.get("events_failed", 0) + 1
            await self._redis.xadd(
                settings.notification_failure_stream_name,
                {
                    "source_stream": stream_name,
                    "message_id": message_id,
                    "error": str(exc),
                    "payload": json.dumps(fields),
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            # Ack poison messages after sending to failure stream to avoid hard blocking.
            await self._redis.xack(stream_name, settings.notification_consumer_group, message_id)

    async def _handle_execution_result(self, fields: dict[str, str]) -> None:
        tenant_id = fields.get("tenant_id")
        user_id = fields.get("user_id")
        status_value = (fields.get("status") or "unknown").lower()

        if not tenant_id or not user_id:
            raise ValueError("execution_results event missing tenant_id/user_id")

        target = await self._resolve_target(UUID(tenant_id), UUID(user_id))
        is_success = status_value in {"success", "filled", "completed"}

        payload = {
            "event_type": "trade_execution",
            "severity": "info" if is_success else "warning",
            "tenant_id": tenant_id,
            "user_id": user_id,
            "channel": target.channel,
            "destination": target.destination,
            "trade_status": status_value,
            "message": (
                "Trade executed successfully"
                if is_success
                else f"Trade execution failed: {fields.get('error', 'unknown reason')}"
            ),
            "meta": fields,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._dispatcher.dispatch(channel=target.channel, payload=payload, urgent=False)

    async def _handle_auth_error(self, fields: dict[str, str]) -> None:
        tenant_id = fields.get("tenant_id")
        user_id = fields.get("user_id")
        if not tenant_id:
            raise ValueError("auth_errors event missing tenant_id")

        target = await self._resolve_target(
            UUID(tenant_id),
            UUID(user_id) if user_id else None,
            prefer_urgent=True,
        )

        payload = {
            "event_type": "auth_2fa_failed",
            "severity": "urgent",
            "tenant_id": tenant_id,
            "user_id": user_id,
            "channel": target.channel,
            "destination": target.destination,
            "message": "Urgent: Zerodha 2FA login failed. Please re-check your credentials immediately.",
            "meta": fields,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._dispatcher.dispatch(channel=target.channel, payload=payload, urgent=True)

    async def _resolve_target(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        prefer_urgent: bool = False,
    ) -> NotificationTarget:
        async with AsyncSessionLocal() as session:
            destination = None
            channel = None

            if user_id is not None:
                pref = await session.scalar(
                    select(NotificationPreference).where(
                        NotificationPreference.tenant_id == tenant_id,
                        NotificationPreference.user_id == user_id,
                        NotificationPreference.is_enabled.is_(True),
                    )
                )
                if pref is not None:
                    channel = pref.channel.lower()
                    destination = pref.destination

            if not destination:
                tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
                if tenant is None:
                    raise ValueError(f"Tenant not found: {tenant_id}")
                # Fallback destination uses org id for n8n-side routing.
                destination = tenant.clerk_org_id
                channel = "email"

        if prefer_urgent and settings.n8n_urgent_webhook_url:
            return NotificationTarget(channel=channel or "email", destination=destination)

        return NotificationTarget(channel=channel or "email", destination=destination)


notification_agent = NotificationAgent()
app = FastAPI(title="Orra Notification Agent")


@app.on_event("startup")
async def startup_event() -> None:
    logging.basicConfig(level=logging.INFO)
    app.state.task = asyncio.create_task(notification_agent.run())


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await notification_agent.stop()
    task: asyncio.Task[None] = app.state.task
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@app.get("/health", tags=["system"])
async def health() -> dict[str, object]:
    return notification_agent.health.payload()


@app.get("/ready", tags=["system"])
async def ready() -> dict[str, bool]:
    return {"ready": notification_agent.health.ready}
