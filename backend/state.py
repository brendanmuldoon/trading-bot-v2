"""Bot state singleton helpers (§9.3): state persists across restarts."""

from sqlalchemy.orm import Session

from backend.models import BotState, BotStateRow


def ensure_bot_state(session: Session, configured: bool) -> BotStateRow:
    """Create or update the singleton row at startup.

    Unconfigured always wins (no keys, scheduler off). A previously
    UNCONFIGURED bot that now has keys starts RUNNING; PAUSED/HALTED
    persist across restarts.
    """
    row = session.get(BotStateRow, 1)
    if row is None:
        row = BotStateRow(
            id=1,
            state=BotState.RUNNING if configured else BotState.UNCONFIGURED,
        )
        session.add(row)
    elif not configured:
        row.state = BotState.UNCONFIGURED
    elif row.state == BotState.UNCONFIGURED:
        row.state = BotState.RUNNING
    session.commit()
    return row
