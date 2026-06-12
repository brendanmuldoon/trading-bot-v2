"""FastAPI app entrypoint.

T03 stub: serves `/api/status` for the compose healthcheck. DBOS
initialization, lifespan wiring, and the events helper arrive in T05.
"""

from functools import lru_cache

from fastapi import FastAPI

from backend.config import Settings, load_settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


app = FastAPI(title="T212 Bot")


@app.get("/api/status")
def status() -> dict[str, object]:
    settings = get_settings()
    return {
        "state": "UNCONFIGURED" if settings.unconfigured else "RUNNING",
        "environment": settings.t212_env.upper(),
        "configured": not settings.unconfigured,
    }
