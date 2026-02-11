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

## Agent Services
Run auth agent (health: `:8010/health`, ready: `:8010/ready`):
```bash
uv run uvicorn src.agents.auth_service:app --host 0.0.0.0 --port 8010
```

Run ticker agent (health: `:8020/health`, ready: `:8020/ready`):
```bash
uv run uvicorn src.agents.ticker_service:app --host 0.0.0.0 --port 8020
```
