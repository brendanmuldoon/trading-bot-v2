"""Events helper (T05): one call writes an `events` row and emits a
structured log line. The UI WebSocket publish hook is added in T36.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from backend.models import Event, EventLevel

logger = logging.getLogger("bot.events")

_session_factory: sessionmaker[Session] | None = None


def configure(session_factory: sessionmaker[Session]) -> None:
    global _session_factory
    _session_factory = session_factory


def log_event(
    level: EventLevel,
    code: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> None:
    """Write an events row (when the DB is configured) and always log."""
    logger.log(
        logging.getLevelNamesMapping().get(level.value, logging.INFO),
        "%s",
        json.dumps({"code": code, "message": message, "context": context or {}}),
    )
    if _session_factory is None:
        return
    with _session_factory() as session:
        session.add(
            Event(
                ts=datetime.now(UTC),
                level=level,
                code=code,
                message=message,
                context=context,
            )
        )
        session.commit()
