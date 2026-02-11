# Orra Trading Platform

Backend foundation for a SaaS trading platform.

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
uv run uvicorn src.api.main:app --reload
```

Health check: `http://127.0.0.1:8000/health`

## Management API (Phase 4)
- `PUT /api/v1/account/kite-credentials`
- `GET /api/v1/account/kite-credentials/status`
- `POST /api/v1/account/kite/check-connection`
- `GET /api/v1/profile/trading`
- `PUT /api/v1/profile/trading`
- `PATCH /api/v1/profile/trading/master-switch`
- `GET /api/v1/admin/tenants/active` (Super Admin)
- `GET /api/v1/admin/system/health` (Super Admin)

## Agent Services
Run auth agent (health: `:8010/health`, ready: `:8010/ready`):
```bash
uv run uvicorn src.agents.auth_service:app --host 0.0.0.0 --port 8010
```

Run ticker agent (health: `:8020/health`, ready: `:8020/ready`):
```bash
uv run uvicorn src.agents.ticker_service:app --host 0.0.0.0 --port 8020
```
