"""GET /api/status (T05): bot state, environment badge, configured flag."""

from fastapi import APIRouter, Request

from backend.config import Settings
from backend.models import BotState, BotStateRow

router = APIRouter()


@router.get("/api/status")
def status(request: Request) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    state = BotState.UNCONFIGURED if settings.unconfigured else BotState.RUNNING
    halted_reason: str | None = None

    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is not None:
        with session_factory() as session:
            row = session.get(BotStateRow, 1)
            if row is not None:
                state = row.state
                halted_reason = row.halted_reason

    return {
        "state": state.value,
        "environment": settings.t212_env.upper(),
        "configured": not settings.unconfigured,
        "halted_reason": halted_reason,
    }
