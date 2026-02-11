from fastapi import FastAPI

from src.api.middleware import tenant_context_middleware

app = FastAPI(title="Orra Trading Platform")
app.middleware("http")(tenant_context_middleware)


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
