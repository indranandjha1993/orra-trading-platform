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
cp .env.example .env
docker compose up -d
uv run uvicorn src.api.main:app --reload
```

Health check: `http://127.0.0.1:8000/health`
