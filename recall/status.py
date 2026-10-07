"""Status snapshot: python -m recall.status

v4 (Phase 5C): daily goal progress, streak, active-session state, due
count, LLM call count, and sessions pending approval.
"""

from datetime import UTC, datetime

from recall.config import settings
from recall.db import create_db_and_tables, get_session
from recall.heartbeat import last_tick_at
from recall.llm import calls_used_today
from recall.questions import list_pending
from recall.scoring import answered_today_count, current_streak_days, daily_goal_met
from recall.session import due_questions, has_active_session, open_dispatch_for


def get_status() -> dict:
    now = datetime.now(UTC)
    db = get_session()
    try:
        active_run = has_active_session(db)
        session_state = "idle"
        current_question_id = None
        if active_run is not None:
            session_state = f"in session: question {active_run.questions_asked} of up to {settings.daily_quota}"
            open_dispatch = open_dispatch_for(db, active_run.id)
            if open_dispatch is not None:
                current_question_id = open_dispatch.question_id

        due_count = len(due_questions(db, now))
        answered_today = answered_today_count(db, now)
        goal_met = daily_goal_met(db, now)
    finally:
        db.close()

    pending_sessions = list_pending()
    last_tick = last_tick_at()
    last_tick_ago_s = (now - last_tick).total_seconds() if last_tick is not None else None

    return {
        "daily_quota": settings.daily_quota,
        "answered_today": answered_today,
        "goal_met": goal_met,
        "streak_days": current_streak_days(now),
        "session_state": session_state,
        "current_question_id": current_question_id,
        "due_count": due_count,
        "llm_calls_used_today": calls_used_today(),
        "pending_session_ids": [s.id for s in pending_sessions],
        "last_tick_ago_s": last_tick_ago_s,
    }


def main() -> None:
    create_db_and_tables()
    status = get_status()

    goal = "met" if status["goal_met"] else "not yet met"
    print(f"Daily goal: {status['answered_today']} of {status['daily_quota']} ({goal})")
    print(f"Streak: {status['streak_days']} day(s)")
    question_suffix = f" (question {status['current_question_id']})" if status["current_question_id"] else ""
    print(f"Session: {status['session_state']}{question_suffix}")
    print(f"Due questions: {status['due_count']}")
    print(f"LLM calls used today: {status['llm_calls_used_today']}")
    if status["last_tick_ago_s"] is None:
        print("daemon last tick: never")
    else:
        print(f"daemon last tick: {status['last_tick_ago_s']:.0f}s ago")
    if status["pending_session_ids"]:
        print(f"Sessions pending approval: {status['pending_session_ids']}")
    else:
        print("Sessions pending approval: none")


if __name__ == "__main__":
    main()
