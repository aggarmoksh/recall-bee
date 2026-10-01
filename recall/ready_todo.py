"""recall/ready_todo.py: the persistent "tick when ready" todo (Phase 5C
Step 2). Recreated whenever it's ticked or goes missing (deleted).
Polled every 10s -- tighter than the main 60s ingest poll, since this is
the user's primary way to start a mini-session.
"""

from sqlmodel import select

from recall.bee.client import BeeClient
from recall.db import ReadyTodo, get_session

READY_TODO_TEXT = "Recall: tick when you can talk"


def _create_and_track(db, client: BeeClient) -> int:
    resp = client.create_todo(READY_TODO_TEXT)
    db.add(ReadyTodo(bee_todo_id=resp["id"]))
    db.commit()
    return resp["id"]


def ensure_ready_todo(client: BeeClient) -> int:
    """Returns the current tracked ready-todo's bee_todo_id, creating one
    if none is tracked yet."""
    db = get_session()
    try:
        row = db.exec(select(ReadyTodo)).first()
        if row is not None:
            return row.bee_todo_id
        return _create_and_track(db, client)
    finally:
        db.close()


def check_and_recreate(client: BeeClient) -> dict | None:
    """Polls the tracked ready-todo. If it's been ticked (completed) or
    has gone missing (deleted on Bee's side), recreates a fresh one and
    returns an event describing what happened -- a "tick_detected" event
    is the caller's cue to start a mini-session. Returns None if the
    existing todo is untouched."""
    db = get_session()
    try:
        row = db.exec(select(ReadyTodo)).first()
        if row is None:
            _create_and_track(db, client)
            return None

        try:
            todo = client.get_todo(row.bee_todo_id)
        except Exception:
            # Any fetch failure (404 -- deleted, or a transient error) is
            # treated as "gone": recreate rather than get stuck forever.
            db.delete(row)
            db.commit()
            _create_and_track(db, client)
            return {"event": "ready_todo_recreated", "reason": "missing"}

        if todo.get("completed"):
            db.delete(row)
            db.commit()
            _create_and_track(db, client)
            return {"event": "tick_detected"}

        return None
    finally:
        db.close()
