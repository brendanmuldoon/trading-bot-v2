"""T05: bot_state startup transitions (§9.3 — state persists across restarts)."""

from sqlalchemy.orm import Session

from backend.models import BotState, BotStateRow
from backend.state import ensure_bot_state


def _set_state(db_session: Session, state: BotState) -> None:
    row = db_session.get(BotStateRow, 1)
    if row is None:
        row = BotStateRow(id=1)
        db_session.add(row)
    row.state = state
    db_session.commit()


def test_first_boot_unconfigured(db_session: Session) -> None:
    row = db_session.get(BotStateRow, 1)
    if row is not None:
        db_session.delete(row)
        db_session.commit()
    assert ensure_bot_state(db_session, configured=False).state == BotState.UNCONFIGURED


def test_first_boot_configured_runs(db_session: Session) -> None:
    row = db_session.get(BotStateRow, 1)
    if row is not None:
        db_session.delete(row)
        db_session.commit()
    assert ensure_bot_state(db_session, configured=True).state == BotState.RUNNING


def test_paused_persists_across_restart(db_session: Session) -> None:
    _set_state(db_session, BotState.PAUSED)
    assert ensure_bot_state(db_session, configured=True).state == BotState.PAUSED


def test_halted_persists_across_restart(db_session: Session) -> None:
    _set_state(db_session, BotState.HALTED)
    assert ensure_bot_state(db_session, configured=True).state == BotState.HALTED


def test_unconfigured_overrides_previous_state(db_session: Session) -> None:
    _set_state(db_session, BotState.RUNNING)
    assert ensure_bot_state(db_session, configured=False).state == BotState.UNCONFIGURED


def test_newly_configured_resumes_running(db_session: Session) -> None:
    _set_state(db_session, BotState.UNCONFIGURED)
    assert ensure_bot_state(db_session, configured=True).state == BotState.RUNNING
