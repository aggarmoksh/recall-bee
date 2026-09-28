"""Daemon: python -m recall.run

Runs the full loop in one process: ingestion (stream + periodic poll),
session building (part of ingest's rebuild_sessions), question generation
for newly-closed sessions, dispatch, answer flow, and feedback-todo cleanup.

--dry-run: does everything except POST/DELETE todos and LLM calls (reads
still happen -- ingestion keeps polling/streaming real Bee data -- but
generation, dispatch's todo POST, grading, and cleanup's todo DELETE are all
skipped with a log line instead).

Ctrl-C must leave no orphaned bee.exe processes: every Bee subprocess this
module touches goes through recall/bee/process.py's tree-kill-first context
managers, and asyncio task cancellation still runs their `finally` blocks.
"""

import argparse
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlmodel import select

from recall.answer_flow import cleanup_old_feedback_todos, process_dispatch_answer
from recall.bee.client import BeeClient
from recall.bee.process import BeeProxy, find_bee_processes
from recall.config import settings
from recall.db import Answer, Dispatch, Session, create_db_and_tables, get_session
from recall.dispatch import dispatch_next
from recall.ingest import consume_stream_live, poll_once
from recall.matcher import DEFAULT_HARD_CAP_S
from recall.overrides import load_excluded_sessions
from recall.questions import generate_for_session

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "recall-run.log"

SESSION_CLOSED_GAP_MINUTES = 15
LOOP_INTERVAL_S = 60
ANSWER_WINDOW_SAFETY_BUFFER_S = 30  # on top of the matcher's own hard cap

logger = logging.getLogger("recall.run")


def _log_line(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    line = f"{timestamp}  {message}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def find_closed_unclassified_sessions(db, now: datetime) -> list[Session]:
    """A session is 'closed' once its last utterance is >=15 min old and it
    hasn't been classified yet. Excluded sessions are skipped entirely."""
    excluded = load_excluded_sessions()
    cutoff = now - timedelta(minutes=SESSION_CLOSED_GAP_MINUTES)
    sessions = db.exec(select(Session).where(Session.session_type.is_(None))).all()
    return [s for s in sessions if s.id not in excluded and s.end_at is not None and s.end_at <= cutoff]


def find_dispatches_ready_for_answer_flow(db, now: datetime) -> list[Dispatch]:
    """An open dispatch is ready once its answer window is guaranteed
    closed: alarm_at + the matcher's hard cap + a safety buffer."""
    ready = []
    open_dispatches = db.exec(select(Dispatch).where(Dispatch.status.in_(["pending", "delivered"]))).all()
    for d in open_dispatches:
        if db.exec(select(Answer).where(Answer.dispatch_id == d.id)).first() is not None:
            continue
        if now >= d.alarm_at + timedelta(seconds=DEFAULT_HARD_CAP_S + ANSWER_WINDOW_SAFETY_BUFFER_S):
            ready.append(d)
    return ready


def run_once(dry_run: bool = False, force_dispatch: bool = False) -> None:
    now = datetime.now(UTC)

    with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
        counts = poll_once(client)
    _log_line(f"ingest: {counts['conversations_touched']} conversation(s) touched, {counts['utterances_added']} utterance(s) added")

    db = get_session()
    try:
        closed = find_closed_unclassified_sessions(db, now)
    finally:
        db.close()
    for session in closed:
        if dry_run:
            _log_line(f"would generate questions for session {session.id} (skipped, dry-run)")
            continue
        try:
            questions = generate_for_session(session.id)
            db = get_session()
            s = db.get(Session, session.id)
            db.close()
            _log_line(f"generated {len(questions)} question(s) for session {session.id} (type={s.session_type}, approval={s.approval_status})")
        except Exception as e:
            _log_line(f"generation FAILED for session {session.id}: {e}")

    if dry_run:
        dispatch = dispatch_next(dry_run=True, now=now)
        if dispatch:
            _log_line(f"would dispatch question {dispatch.question_id} (dry-run, no real todo posted)")
    else:
        dispatch = dispatch_next(now=now, force=force_dispatch)
        if dispatch:
            _log_line(f"dispatched question {dispatch.question_id} as todo {dispatch.bee_todo_id}, alarm_at={dispatch.alarm_at.isoformat()}")

    db = get_session()
    try:
        ready = find_dispatches_ready_for_answer_flow(db, now)
    finally:
        db.close()
    for d in ready:
        if dry_run:
            _log_line(f"would grade dispatch {d.id} (skipped, dry-run)")
            continue
        try:
            answer = process_dispatch_answer(d.id)
            _log_line(f"graded dispatch {d.id}: verdict={answer.verdict}, quality={answer.quality} -- question todo deleted, feedback posted")
        except Exception as e:
            _log_line(f"answer flow FAILED for dispatch {d.id}: {e}")

    if dry_run:
        _log_line("would clean up old feedback todos (skipped, dry-run)")
    else:
        count = cleanup_old_feedback_todos(now=now)
        if count:
            _log_line(f"cleaned up {count} feedback todo(s) older than 12h")


async def run_daemon(dry_run: bool = False, force_dispatch: bool = False) -> None:
    _log_line(f"recall.run starting (dry_run={dry_run})")
    orphans = find_bee_processes()
    if orphans:
        _log_line(f"WARNING: {len(orphans)} pre-existing bee process(es) found before starting: {[o['pid'] for o in orphans]}")

    async def poll_loop():
        first = True
        while True:
            try:
                await asyncio.get_running_loop().run_in_executor(None, run_once, dry_run, force_dispatch and first)
            except Exception as e:
                _log_line(f"run_once FAILED: {e}")
            first = False
            await asyncio.sleep(LOOP_INTERVAL_S)

    # consume_stream_live() (recall.ingest) already tree-kills its own bee
    # stream subprocess in a finally block, including under cancellation.
    await asyncio.gather(consume_stream_live(), poll_loop())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="do everything except POST/DELETE todos and LLM calls")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit (no live stream)")
    parser.add_argument("--force-dispatch", action="store_true", help="testing only: bypass quiet-hours/cap/gap/open-dispatch checks for one dispatch")
    args = parser.parse_args()

    create_db_and_tables()

    if args.once:
        run_once(dry_run=args.dry_run, force_dispatch=args.force_dispatch)
        return

    try:
        asyncio.run(run_daemon(dry_run=args.dry_run, force_dispatch=args.force_dispatch))
    except KeyboardInterrupt:
        _log_line("Ctrl-C received, shutting down...")


if __name__ == "__main__":
    main()
