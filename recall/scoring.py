"""recall/scoring.py: v4 scoring (Phase 5C Step 5).

no_answer never updates SM-2 (AGENT.md, 2026-09-30) -- a missed
notification/window is not forgetting. `recall.session`'s own grading
path already enforces this going forward; `recompute_past_no_answers()`
fixes the history, since the pre-v4 `recall.answer_flow` path special-cased
only `incomplete_capture`, not `no_answer` -- every historical no_answer
dispatch DID feed quality=0 through sm2() and penalize the review state.

Streak day definition: a "recall day" runs from DAY_RESET to DAY_RESET
(default 04:00 local), not midnight to midnight, so a late-night session
still counts toward the day it started. A day counts toward the streak if
at least one SessionRun reached status "completed" that day (ran to a
natural end -- quota hit or ran out of due questions -- rather than being
cut short by a no_answer).
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import select

from recall.config import settings
from recall.db import Answer, Dispatch, Question, ReviewState, SessionRun, get_session
from recall.srs import sm2


def _day_reset_hour_minute() -> tuple[int, int]:
    h, m = settings.day_reset.split(":")
    return int(h), int(m)


def local_day_for(dt: datetime) -> str:
    """The recall-day a timestamp belongs to: the local calendar day,
    shifted so a new day starts at DAY_RESET rather than midnight."""
    tz = ZoneInfo(settings.timezone)
    local = dt.astimezone(tz)
    reset_h, reset_m = _day_reset_hour_minute()
    shifted = local - timedelta(hours=reset_h, minutes=reset_m)
    return shifted.date().isoformat()


def answered_today_count(db, now: datetime) -> int:
    """Count of real (non-no_answer) answers dispatched on the recall-day
    `now` falls in."""
    today = local_day_for(now)
    answers = db.exec(select(Answer).where(Answer.verdict != "no_answer")).all()
    count = 0
    for a in answers:
        dispatch = db.get(Dispatch, a.dispatch_id)
        if dispatch is not None and local_day_for(dispatch.dispatched_at) == today:
            count += 1
    return count


def daily_goal_met(db, now: datetime) -> bool:
    return answered_today_count(db, now) >= settings.daily_quota


def current_streak_days(now: datetime | None = None) -> int:
    """Consecutive recall-days (counting back from today, or from
    yesterday if today hasn't met the goal yet -- a streak isn't broken
    until a full day passes unmet) with at least one completed session."""
    now = now or datetime.now(UTC)
    db = get_session()
    try:
        completed_runs = db.exec(select(SessionRun).where(SessionRun.status == "completed")).all()
    finally:
        db.close()

    completed_days = {local_day_for(r.ended_at) for r in completed_runs if r.ended_at is not None}
    if not completed_days:
        return 0

    today = local_day_for(now)
    cursor_date = datetime.fromisoformat(today).date()
    if today not in completed_days:
        cursor_date -= timedelta(days=1)

    streak = 0
    while cursor_date.isoformat() in completed_days:
        streak += 1
        cursor_date -= timedelta(days=1)
    return streak


def recompute_past_no_answers(now: datetime | None = None) -> list[dict]:
    """Rebuilds ReviewState for every question that has ever had a
    no_answer, by replaying its full dispatch history in order and
    applying sm2() only to real grades (correct/partial/incorrect/
    not_an_answer) -- skipping no_answer and incomplete_capture, matching
    the current rule. Returns a report of every question whose
    ReviewState actually changed (before/after), touches nothing else."""
    now = now or datetime.now(UTC)
    db = get_session()
    changed: list[dict] = []
    try:
        no_answer_answers = db.exec(select(Answer).where(Answer.verdict == "no_answer")).all()
        affected_question_ids: set[int] = set()
        for a in no_answer_answers:
            dispatch = db.get(Dispatch, a.dispatch_id)
            if dispatch is not None:
                affected_question_ids.add(dispatch.question_id)

        for question_id in sorted(affected_question_ids):
            review_state = db.exec(select(ReviewState).where(ReviewState.question_id == question_id)).first()
            before = (
                {
                    "ease_factor": review_state.ease_factor,
                    "interval_days": review_state.interval_days,
                    "repetitions": review_state.repetitions,
                    "due_at": review_state.due_at.isoformat(),
                }
                if review_state is not None
                else None
            )

            dispatches = db.exec(
                select(Dispatch).where(Dispatch.question_id == question_id).order_by(Dispatch.dispatched_at)
            ).all()
            ef, interval, reps = 2.5, 0, 0
            last_due = None
            had_real_review = False
            for d in dispatches:
                answer = db.exec(select(Answer).where(Answer.dispatch_id == d.id)).first()
                if answer is None or answer.verdict in ("no_answer", "incomplete_capture"):
                    continue
                result = sm2(answer.quality, ef, interval, reps)
                ef, interval, reps = result.ease_factor, result.interval_days, result.repetitions
                last_due = answer.matched_at + timedelta(days=result.interval_days)
                had_real_review = True

            if review_state is None:
                if not had_real_review:
                    continue  # nothing to create -- this question has never had a real grade
                review_state = ReviewState(question_id=question_id)

            review_state.ease_factor = ef
            review_state.interval_days = interval
            review_state.repetitions = reps
            review_state.due_at = last_due if last_due is not None else now
            db.add(review_state)
            db.commit()
            db.refresh(review_state)

            after = {
                "ease_factor": review_state.ease_factor,
                "interval_days": review_state.interval_days,
                "repetitions": review_state.repetitions,
                "due_at": review_state.due_at.isoformat(),
            }
            if before != after:
                changed.append({"question_id": question_id, "before": before, "after": after})

        return changed
    finally:
        db.close()


def main() -> None:
    changed = recompute_past_no_answers()
    print(f"recomputed {len(changed)} question(s) whose ReviewState changed:")
    for c in changed:
        print(f"  question {c['question_id']}: {c['before']} -> {c['after']}")


if __name__ == "__main__":
    main()
