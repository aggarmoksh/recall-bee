"""Daemon liveness (Phase 5G Step 4, AGENT.md 2026-10-07): the fast loop
records a tick here every cycle. recall.status and /api/today read it back
to tell "process is running but wedged" apart from "process is actually
dead" -- a distinction the OS process list alone can't make, and the
distinction that mattered for both 2-day-check incidents (FRICTION_LOG.md)."""

from datetime import UTC, datetime

from recall.db import Heartbeat, get_session


def record_tick(now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    db = get_session()
    try:
        hb = db.get(Heartbeat, 1)
        if hb is None:
            hb = Heartbeat(id=1, last_tick_at=now)
        else:
            hb.last_tick_at = now
        db.add(hb)
        db.commit()
    finally:
        db.close()


def last_tick_at() -> datetime | None:
    db = get_session()
    try:
        hb = db.get(Heartbeat, 1)
        return hb.last_tick_at if hb is not None else None
    finally:
        db.close()
