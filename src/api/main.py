from fastapi import FastAPI

app = FastAPI(title="Orra Trading Platform")


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
