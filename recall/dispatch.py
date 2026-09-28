"""Dispatcher: picks the next due question and posts it as a Bee todo.

Implements AGENT.md's "Loop policy" dispatch rules:
- at most 6 dispatches/day
- quiet hours 22:00-08:00 local (no dispatch during this window)
- at least 45 minutes between dispatches
- at most ONE open dispatch at a time (an earlier one must be answered/
  expired before the next goes out)
- first review ~2h after the session ends, then next day, then SM-2
  thereafter (via ReviewState)

Idempotent and restart-safe: all state lives in the DB (Dispatch rows),
nothing is held in memory between calls.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import select

from recall.bee.client import BeeClient
from recall.bee.process import BeeProxy
from recall.config import settings
from recall.db import Dispatch, Question, ReviewState, get_session
from recall.srs import DEFAULT_EASE_FACTOR

# Dispatch pacing profiles (AGENT.md "Loop policy", RECALL_PROFILE in .env):
# "normal" is the steady-state profile; "dense" is the trial's Day 1 profile
# (tighter pacing, faster first review, higher daily cap). Quiet hours and
# the single-open-dispatch rule apply to both and are NOT profile-dependent.
MAX_DISPATCHES_PER_DAY = 6  # "normal" default, kept as a module constant so
MIN_GAP_MINUTES = 45  # existing tests that import these directly (and run
FIRST_REVIEW_DELAY_HOURS = 2.0  # under the default "normal" profile) keep working.
PROFILES = {
    "normal": {
        "max_per_day": MAX_DISPATCHES_PER_DAY,
        "min_gap_minutes": MIN_GAP_MINUTES,
        "first_review_hours": FIRST_REVIEW_DELAY_HOURS,
    },
    "dense": {
        "max_per_day": 25,
        "min_gap_minutes": 15,
        "first_review_hours": 0.5,
    },
}
QUIET_HOURS_START = 22  # 22:00 local
QUIET_HOURS_END = 8  # 08:00 local
SECOND_REVIEW_DELAY_DAYS = 1


def active_profile(profile: str | None = None) -> dict:
    """Resolves the pacing profile to use: an explicit `profile` name (for
    tests/callers that want a specific one), or settings.recall_profile.
    Raises on an invalid name rather than silently falling back -- pacing
    rules are part of the Loop policy, not something to guess about."""
    name = profile or settings.recall_profile
    if name not in PROFILES:
        raise ValueError(f"invalid RECALL_PROFILE {name!r} (valid: {sorted(PROFILES)})")
    return PROFILES[name]

# Bee todo text length: kept short deliberately -- notifications truncate on
# a Watch/lock-screen banner. Observed comfortably fitting length; see
# phase4B report for the exact number of characters used in practice.
TODO_TEXT_MAX_CHARS = 120


def to_local(dt: datetime) -> datetime:
    return dt.astimezone(ZoneInfo(settings.timezone))


def _in_quiet_hours(dt: datetime) -> bool:
    hour = to_local(dt).hour
    if QUIET_HOURS_START > QUIET_HOURS_END:
        return hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END
    return QUIET_HOURS_START <= hour < QUIET_HOURS_END


def _todo_text(question: Question) -> str:
    text = f"Recall: {question.text}"
    if len(text) > TODO_TEXT_MAX_CHARS:
        text = text[: TODO_TEXT_MAX_CHARS - 1].rstrip() + "…"
    return text


def dispatches_today(db, now: datetime) -> list[Dispatch]:
    """'Today' means the local calendar day (the 6/day cap is a local-day
    cap, same as quiet hours). Dispatch.dispatched_at is stored in UTC, so
    compare in local time and convert the local midnight boundary back."""
    local_now = to_local(now)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day_utc = local_midnight.astimezone(UTC)
    return db.exec(select(Dispatch).where(Dispatch.dispatched_at >= start_of_day_utc)).all()


def has_open_dispatch(db) -> Dispatch | None:
    """An 'open' dispatch is one with no Answer yet and not expired."""
    from recall.db import Answer

    dispatches = db.exec(select(Dispatch).where(Dispatch.status.in_(["pending", "delivered"]))).all()
    for d in dispatches:
        answer = db.exec(select(Answer).where(Answer.dispatch_id == d.id)).first()
        if answer is None:
            return d
    return None


def _is_approved(db, question: Question) -> bool:
    """A question is dispatchable only if its Session's approval_status is
    'approved' -- substantive_discussion sessions start 'pending' and must
    not be dispatched until the operator approves them via --approve."""
    if question.session_id is None:
        return False
    from recall.db import Session as SessionModel

    session = db.get(SessionModel, question.session_id)
    return session is not None and session.approval_status == "approved"


def next_due_question(db, now: datetime) -> Question | None:
    """Picks the question with the earliest due ReviewState.due_at among
    approved questions that haven't been dispatched yet or are due again."""
    already_dispatched_question_ids = {d.question_id for d in db.exec(select(Dispatch)).all()}

    # Never-dispatched approved questions are due immediately, ordered by id
    # (stable, deterministic order -- oldest-approved-first).
    fresh = db.exec(
        select(Question)
        .where(Question.active == True)  # noqa: E712
        .where(Question.session_id.is_not(None))
        .order_by(Question.id)
    ).all()
    for q in fresh:
        if q.id not in already_dispatched_question_ids and _is_approved(db, q):
            return q

    # Otherwise, the earliest-due ReviewState among approved, active questions.
    review_states = db.exec(select(ReviewState).where(ReviewState.due_at <= now).order_by(ReviewState.due_at)).all()
    for rs in review_states:
        q = db.get(Question, rs.question_id)
        if q is not None and q.active and _is_approved(db, q):
            return q
    return None


FORCE_ALARM_DELAY_SECONDS = 20


def compute_next_alarm(db, question: Question, now: datetime, force: bool = False, profile: str | None = None) -> datetime:
    """First review (no ReviewState yet) is `first_review_hours` (per the
    active pacing profile) after the session ends. Every review after that
    uses ReviewState.due_at, which recall.grading's SM-2 call already sets
    correctly: repetitions 0->1 naturally yields a 1-day interval (SM-2's
    own rule), matching the policy's "second review next day" -- no
    separate case is needed once a ReviewState exists.

    `force` (testing only): the whole point of --force-dispatch is a live
    buzz-now-answer-in-15-20s test, so a forced dispatch's alarm is "now"
    (plus a short buffer for the real Bee notification to land), not the
    normal profile/SM-2 schedule."""
    if force:
        return now + timedelta(seconds=FORCE_ALARM_DELAY_SECONDS)
    review_state = db.exec(select(ReviewState).where(ReviewState.question_id == question.id)).first()
    if review_state is None:
        from recall.db import Session as SessionModel

        session = db.get(SessionModel, question.session_id) if question.session_id else None
        anchor = session.end_at if (session and session.end_at) else now
        first_review_hours = active_profile(profile)["first_review_hours"]
        return max(anchor + timedelta(hours=first_review_hours), now)
    return max(review_state.due_at, now)


def dispatch_next(
    dry_run: bool = False, now: datetime | None = None, force: bool = False, profile: str | None = None
) -> Dispatch | None:
    """Attempts one dispatch cycle: enforces the daily cap, quiet hours,
    min-gap, and single-open-dispatch rules, then posts a todo for the next
    due question. Returns the created Dispatch, or None if nothing was
    dispatched. `now` is injectable for testing; defaults to the real time.
    `profile` picks the pacing profile ("normal"/"dense"); defaults to
    settings.recall_profile.

    `force` (testing only, Phase 4B Step 5's --force-dispatch): bypasses
    quiet-hours/daily-cap/min-gap pacing rules, but still refuses if there
    is already an open dispatch -- that invariant is a correctness
    requirement (the matcher assumes one active question), not pacing."""
    now = now or datetime.now(UTC)
    active = active_profile(profile)
    db = get_session()
    try:
        if not force and _in_quiet_hours(now):
            return None

        if has_open_dispatch(db) is not None:
            return None

        todays = dispatches_today(db, now)
        if not force and len(todays) >= active["max_per_day"]:
            return None

        if not force and todays:
            last = max(d.dispatched_at for d in todays)
            if now - last < timedelta(minutes=active["min_gap_minutes"]):
                return None

        question = next_due_question(db, now)
        if question is None:
            return None

        alarm_at = compute_next_alarm(db, question, now, force=force, profile=profile)
        text = _todo_text(question)

        if dry_run:
            # Distinct negative placeholder id -- clearly not a real Bee todo,
            # and unique enough across calls to satisfy the unique constraint.
            bee_todo_id = -int(now.timestamp() * 1000)
        else:
            with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
                resp = client.create_todo(text, alarm_at=alarm_at.isoformat(timespec="seconds"))
            bee_todo_id = resp["id"]

        dispatch = Dispatch(
            question_id=question.id,
            bee_todo_id=bee_todo_id,
            alarm_at=alarm_at,
            status="dry_run" if dry_run else "pending",
        )
        db.add(dispatch)

        if db.exec(select(ReviewState).where(ReviewState.question_id == question.id)).first() is None:
            db.add(ReviewState(question_id=question.id, ease_factor=DEFAULT_EASE_FACTOR, due_at=alarm_at))

        db.commit()
        db.refresh(dispatch)
        return dispatch
    finally:
        db.close()
