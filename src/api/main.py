from fastapi import FastAPI

from src.api.middleware import tenant_context_middleware
from src.api.routes.connections import router as connections_router

app = FastAPI(title="Orra Trading Platform")
app.middleware("http")(tenant_context_middleware)
app.include_router(connections_router, prefix="/api/v1")


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
