# Orra Trading Platform: Multi-Tenant SaaS Trading Backend

Production-oriented backend foundation for a multi-tenant algorithmic trading SaaS.

It provides:
- tenant-isolated APIs with auth and role enforcement
- secure credential handling (encrypted at rest)
- event-driven background agents for auth token refresh, market ticker ingestion, and user notifications
- subscription-aware feature gating with billing webhooks

## Stack
- FastAPI
- PostgreSQL 15
- Redis 7
- TimescaleDB (PostgreSQL 15)
- SQLAlchemy (async)
- Alembic

## Project Structure
```
src/
  api/      # FastAPI app layer
  core/     # configuration, DB, security core services
  agents/   # domain agents (auth, ticker, strategy)
  models/   # SQLAlchemy models
```

## Setup
```bash
uv sync
uv run playwright install chromium
cp .env.example .env
docker compose up -d
uv run alembic upgrade head
uv run uvicorn src.api.main:app --reload
```

Health check: `http://127.0.0.1:8000/health`

## Required Environment Variables
Set these in `.env` before running full flows:
- `MASTER_ENCRYPTION_KEY`
- `CLERK_JWKS_URL`, `CLERK_ISSUER`, `CLERK_SECRET_KEY`
- `KITE_API_KEY`, `KITE_API_SECRET` (if used by your flow)
- `ZERODHA_USER_ID_MAP_JSON`
- `ZERODHA_PASSWORD_MAP_JSON`
- `TICKER_INSTRUMENT_TOKENS_CSV`
- `N8N_TELEGRAM_WEBHOOK_URL`, `N8N_WHATSAPP_WEBHOOK_URL`, `N8N_EMAIL_WEBHOOK_URL`
- `N8N_URGENT_WEBHOOK_URL`

## Management API (Phase 4)
- `PUT /api/v1/account/kite-credentials`
- `GET /api/v1/account/kite-credentials/status`
- `POST /api/v1/account/kite/check-connection`
- `POST /api/v1/connections/kite/test` (tenant-scoped connection test by `user_id`)
- `GET /api/v1/profile/trading`
- `PUT /api/v1/profile/trading`
- `PATCH /api/v1/profile/trading/master-switch`
- `GET /api/v1/admin/tenants/active` (Super Admin)
- `GET /api/v1/admin/system/health` (Super Admin)

## Billing & Subscription (Phase 5)
- `GET /api/v1/billing/entitlements`
- `POST /api/v1/billing/guards/strategy` (Basic: 1 active strategy, Pro: unlimited)
- `POST /api/v1/billing/guards/trade` (Basic: 5/day, Pro: unlimited)
- `POST /api/v1/billing/guards/priority` (Pro only)
- `POST /api/v1/webhooks/billing` (`invoice.paid`, `subscription.deleted`)

On `subscription.deleted`, tenant `is_active` is set to `False`, all tenant strategy instances are deactivated, and runtime Redis state flips to inactive (`tenant:active:{tenant_id}`).

## Agent Services
Run auth agent (health: `:8010/health`, ready: `:8010/ready`):
```bash
uv run uvicorn src.agents.auth_service:app --host 0.0.0.0 --port 8010
```

Run ticker agent (health: `:8020/health`, ready: `:8020/ready`):
```bash
uv run uvicorn src.agents.ticker_service:app --host 0.0.0.0 --port 8020
```

Run notification agent (health: `:8030/health`, ready: `:8030/ready`):
```bash
uv run uvicorn src.agents.notification_service:app --host 0.0.0.0 --port 8030
```

Agent notes:
- Auth agent requires valid tenant credential records plus `ZERODHA_USER_ID_MAP_JSON` and `ZERODHA_PASSWORD_MAP_JSON`.
- Ticker agent requires `TICKER_INSTRUMENT_TOKENS_CSV` and Redis access tokens (`kite:access_token:{tenant_id}`).
- Notification agent requires n8n webhook URLs for channels you enable.

## Testing
Run all tests:
```bash
uv run pytest
```

Run tests with coverage:
```bash
uv run pytest --cov=src --cov-report=term-missing
```

Run a specific test module:
```bash
uv run pytest tests/test_api_routes.py -q
```

## CI (GitHub Actions)
Automated tests run on every pull request and push to `main` via:
- `.github/workflows/tests.yml`

What the workflow does:
- runs on `ubuntu-latest`
- tests against Python `3.12` and `3.13`
- installs dependencies with `uv sync --dev`
- runs `uv run pytest --cov=src --cov-report=term-missing`

## Notification Engine (Phase 7)
- Consumes Redis streams:
  - `execution_results` for successful/failed trades
  - `auth_errors` for urgent auth failures (e.g., 2FA login failure)
- Dispatches Telegram/WhatsApp/Email via n8n webhook URLs.
- Auth agent emits `auth_2fa_failed` events into `auth_errors` when tenant login fails, triggering urgent notifications.

## Audit Script
Use `artifacts/audit.sh` to generate command-backed logs and reports under `artifacts/logs` and `artifacts/reports`.

Host mode (default, full infra checks):
```bash
bash artifacts/audit.sh
```

Sandbox mode (skips Docker/socket checks):
```bash
bash artifacts/audit.sh --sandbox
```
