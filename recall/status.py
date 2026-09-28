"""Status snapshot: python -m recall.status

Prints today's dispatches, the current open dispatch (if any), the next due
question, today's LLM call count, and sessions pending approval.
"""

from datetime import UTC, datetime

from recall.config import settings
from recall.db import create_db_and_tables, get_session
from recall.dispatch import dispatches_today, has_open_dispatch, next_due_question
from recall.llm import calls_used_today
from recall.questions import list_pending


def get_status() -> dict:
    now = datetime.now(UTC)
    db = get_session()
    try:
        todays = dispatches_today(db, now)
        open_dispatch = has_open_dispatch(db)
        next_question = next_due_question(db, now)
    finally:
        db.close()

    pending_sessions = list_pending()

    return {
        "profile": settings.recall_profile,
        "dispatches_today": len(todays),
        "open_dispatch_id": open_dispatch.id if open_dispatch else None,
        "next_due_question_id": next_question.id if next_question else None,
        "next_due_question_text": next_question.text if next_question else None,
        "llm_calls_used_today": calls_used_today(),
        "pending_session_ids": [s.id for s in pending_sessions],
    }


def main() -> None:
    create_db_and_tables()
    status = get_status()

    print(f"Profile: {status['profile']}")
    print(f"Dispatches today: {status['dispatches_today']}")
    if status["open_dispatch_id"] is not None:
        print(f"Open dispatch: id={status['open_dispatch_id']}")
    else:
        print("Open dispatch: none")
    if status["next_due_question_id"] is not None:
        print(f"Next due: [{status['next_due_question_id']}] {status['next_due_question_text']}")
    else:
        print("Next due: none")
    print(f"LLM calls used today: {status['llm_calls_used_today']}")
    if status["pending_session_ids"]:
        print(f"Sessions pending approval: {status['pending_session_ids']}")
    else:
        print("Sessions pending approval: none")


if __name__ == "__main__":
    main()
