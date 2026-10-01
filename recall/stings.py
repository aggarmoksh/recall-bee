"""recall/stings.py: v4 nudges (Phase 5C Step 4, AGENT.md).

Up to MAX_STINGS/day inside ACTIVE_WINDOW, at three time slots (start,
middle, end-90min), plus one event-triggered sting right after a
lecture_study session closes. A sting is only ever a nudge, never a
question -- it's held (skipped this tick, retried next tick) if Bee heard
speech in the last 3 minutes, since silence never means "free" but active
speech does mean "busy". No stings once the daily goal is already met.

Copy rule (AGENT.md): plain words, no emoji/symbols -- Bee rewrites them
away before display anyway.
"""

from datetime import UTC, datetime, timedelta

from sqlmodel import select

from recall.bee.client import BeeClient
from recall.bee.process import BeeProxy
from recall.config import settings
from recall.db import StingTodo, Utterance, get_session
from recall.scoring import daily_goal_met
from recall.session import due_questions

BUSY_WINDOW_S = 180  # 3 min
STING_CLEANUP_MAX_AGE_HOURS = 12

SLOTS = ("start", "middle", "end")


def _parse_window(window: str) -> tuple[tuple[int, int], tuple[int, int]]:
    start_s, end_s = window.split("-")
    sh, sm = (int(x) for x in start_s.split(":"))
    eh, em = (int(x) for x in end_s.split(":"))
    return (sh, sm), (eh, em)


def _minutes(h: int, m: int) -> int:
    return h * 60 + m


def window_today(now_local: datetime) -> tuple[datetime, datetime, str]:
    """Returns (window_start, window_end, day_key) for the ACTIVE_WINDOW
    instance that `now_local` either falls inside, or is approaching
    today. Handles a window that wraps past midnight. `day_key` is the
    calendar date the window STARTS on, used to key StingTodo rows so a
    "13:00-03:00" window is one sting-day, not two."""
    (sh, sm), (eh, em) = _parse_window(settings.active_window)
    start_min, end_min = _minutes(sh, sm), _minutes(eh, em)
    wraps = end_min <= start_min

    today_start = now_local.replace(hour=sh, minute=sm, second=0, microsecond=0)
    if wraps:
        today_end = (now_local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).replace(hour=eh, minute=em)
    else:
        today_end = now_local.replace(hour=eh, minute=em, second=0, microsecond=0)

    if now_local < today_start:
        # Before today's window has started -- check if we're still inside
        # YESTERDAY's wrapped window (e.g. 1am, window 18:00-00:00... no
        # wrap; or 13:00-03:00 wraps, so 1am local is still "yesterday's"
        # window instance).
        if wraps:
            yesterday_start = today_start - timedelta(days=1)
            yesterday_end = today_end - timedelta(days=1)
            if yesterday_start <= now_local < yesterday_end:
                return yesterday_start, yesterday_end, yesterday_start.date().isoformat()
        return today_start, today_end, today_start.date().isoformat()

    return today_start, today_end, today_start.date().isoformat()


def slot_times(window_start: datetime, window_end: datetime) -> dict[str, datetime]:
    duration = window_end - window_start
    return {
        "start": window_start,
        "middle": window_start + duration / 2,
        "end": window_end - timedelta(minutes=90),
    }


def _recently_busy(db, now: datetime) -> bool:
    cutoff = now - timedelta(seconds=BUSY_WINDOW_S)
    recent = db.exec(select(Utterance).where(Utterance.spoken_at >= cutoff).where(Utterance.spoken_at <= now)).first()
    return recent is not None


def _post_todo(client: BeeClient, text: str, local_date: str, slot: str) -> None:
    resp = client.create_todo(text, alarm_at=datetime.now(UTC).isoformat(timespec="seconds"))
    db = get_session()
    try:
        db.add(StingTodo(bee_todo_id=resp["id"], local_date=local_date, slot=slot))
        db.commit()
    finally:
        db.close()


def maybe_send_stings(now: datetime | None = None) -> list[dict]:
    """Checks each time-based slot; sends a sting for any slot whose
    target time has passed, hasn't been sent yet today, and isn't held.
    Returns a list of events for logging."""
    now = now or datetime.now(UTC)
    from zoneinfo import ZoneInfo

    now_local = now.astimezone(ZoneInfo(settings.timezone))

    db = get_session()
    try:
        window_start, window_end, day_key = window_today(now_local)
        if not (window_start <= now_local < window_end):
            return []

        if daily_goal_met(db, now):
            return []

        sent_today = db.exec(select(StingTodo).where(StingTodo.local_date == day_key)).all()
        # MAX_STINGS caps only the three time-based slots -- the
        # post_lecture sting is "plus one" beyond that cap (AGENT.md).
        slot_sent_today = [s for s in sent_today if s.slot in SLOTS]
        if len(slot_sent_today) >= settings.max_stings:
            return []
        sent_slots = {s.slot for s in sent_today}

        due = due_questions(db, now)
        if not due:
            return []

        if _recently_busy(db, now):
            return []  # held -- Bee heard speech recently, retried next tick

        slots = slot_times(window_start, window_end)
        # Pick the LATEST unsent slot whose time has already passed (the
        # slot most relevant to "now"), not the earliest -- so a daemon
        # that starts mid-window (e.g. after a long sleep) catches up to
        # the current slot instead of firing a now-stale "start" nudge.
        candidates = [slot for slot in SLOTS if slot not in sent_slots and now_local >= slots[slot]]
        events = []
        if candidates:
            slot = max(candidates, key=lambda s: slots[s])
            text = f"Recall: {len(due)} questions are buzzing for you. Say Hey Bee, let's do a Recall session when you can talk."
            with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
                _post_todo(client, text, day_key, slot)
            events.append({"event": "sting_sent", "slot": slot, "due_count": len(due)})
        return events
    finally:
        db.close()


def send_post_lecture_sting(now: datetime | None = None) -> dict | None:
    """Event-triggered sting right after a lecture_study session closes
    (called by recall.run once generate_for_session classifies one) --
    separate from the three time-based slots, so it doesn't consume one."""
    now = now or datetime.now(UTC)
    from zoneinfo import ZoneInfo

    now_local = now.astimezone(ZoneInfo(settings.timezone))
    db = get_session()
    try:
        window_start, window_end, day_key = window_today(now_local)
        if not (window_start <= now_local < window_end):
            return None
        if daily_goal_met(db, now):
            return None
        already_sent = db.exec(
            select(StingTodo).where(StingTodo.local_date == day_key).where(StingTodo.slot == "post_lecture")
        ).first()
        if already_sent is not None:
            return None
        due = due_questions(db, now)
        if not due:
            return None

        text = f"Recall: {len(due)} questions are buzzing for you. Say Hey Bee, let's do a Recall session when you can talk."
        with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
            _post_todo(client, text, day_key, "post_lecture")
        return {"event": "sting_sent", "slot": "post_lecture", "due_count": len(due)}
    finally:
        db.close()


def cleanup_old_stings(now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=STING_CLEANUP_MAX_AGE_HOURS)
    db = get_session()
    try:
        old = db.exec(select(StingTodo).where(StingTodo.created_at < cutoff)).all()
        ids = [s.bee_todo_id for s in old]
    finally:
        db.close()

    if not ids:
        return 0

    with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
        for todo_id in ids:
            client.delete_todo(todo_id)

    db = get_session()
    try:
        for s in db.exec(select(StingTodo).where(StingTodo.bee_todo_id.in_(ids))).all():
            db.delete(s)
        db.commit()
    finally:
        db.close()
    return len(ids)
