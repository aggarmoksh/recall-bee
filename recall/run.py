"""Daemon: python -m recall.run

v4 pull-with-nudges model (AGENT.md, Phase 5C). Three concurrent loops:

- the live stream (recall.ingest.consume_stream_live) -- ingests
  utterances in real time AND buffers a short rolling window to detect
  the spoken session-start phrase (variant A) as it's said.
- a fast loop (~10s): polls the ready-todo for a tick, reacts to a
  just-detected spoken start, and advances an active mini-session
  (grades a closed answer window, dispatches the next question, or ends
  the session) -- kept fast so a session feels back-to-back, not laggy.
- a slow loop (~60s): ingest poll, session classification for newly-
  closed sessions (+ a post-lecture sting), time-based stings, old
  feedback/sting-todo cleanup, and sleep/wake recovery.

--dry-run: does everything except POST/DELETE todos and LLM calls.

Ctrl-C must leave no orphaned bee.exe processes: every Bee subprocess this
module touches goes through recall/bee/process.py's tree-kill-first context
managers, and asyncio task cancellation still runs their `finally` blocks.
"""

import argparse
import asyncio
import logging
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlmodel import select

from recall.answer_flow import cleanup_old_feedback_todos
from recall.bee.client import BeeClient
from recall.bee.process import BeeProxy, find_bee_processes
from recall.config import settings
from recall.db import Session, StartPhraseUtterance, create_db_and_tables, get_session
from recall.ingest import consume_stream_live, poll_once
from recall.matcher import detects_session_start
from recall.overrides import load_excluded_sessions
from recall.questions import generate_for_session
from recall.ready_todo import check_and_recreate
from recall.session import advance, due_questions, recover, start_session
from recall.stings import cleanup_old_stings, maybe_send_stings, send_post_lecture_sting

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "recall-run.log"

SESSION_CLOSED_GAP_MINUTES = 15
FAST_LOOP_INTERVAL_S = 10
SLOW_LOOP_INTERVAL_S = 60
START_PHRASE_WINDOW_S = 15  # rolling buffer for detecting a split spoken phrase
SLEEP_GAP_THRESHOLD_S = SLOW_LOOP_INTERVAL_S * 3  # a tick this late means the process (or OS) was suspended

logger = logging.getLogger("recall.run")

_recent_utterances: deque[tuple[int, str, datetime]] = deque()
_pending_spoken_start: list[int] | None = None  # utterance ids that triggered detection, or None


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


def _on_new_utterance(utterance_id: int, text: str, spoken_at: datetime | None) -> None:
    """Called synchronously from the stream consumer for every newly
    ingested utterance (pure in-memory bookkeeping, no I/O -- safe to run
    inline in the async stream loop). Buffers a short rolling window and
    flags a spoken-start detection for the fast loop to react to."""
    global _pending_spoken_start
    now = datetime.now(UTC)
    _recent_utterances.append((utterance_id, text, spoken_at or now))
    cutoff = now - timedelta(seconds=START_PHRASE_WINDOW_S)
    while _recent_utterances and _recent_utterances[0][2] < cutoff:
        _recent_utterances.popleft()

    if _pending_spoken_start is not None:
        return  # already flagged, waiting for the fast loop to consume it
    texts = [t for _, t, _ in _recent_utterances]
    if detects_session_start(texts):
        _pending_spoken_start = [uid for uid, _, _ in _recent_utterances]


def _react_to_trigger(client: BeeClient, trigger: str, now: datetime) -> None:
    db = get_session()
    try:
        would_start = due_questions(db, now)
    finally:
        db.close()

    if not would_start:
        client.create_todo("Recall: nothing due right now, you are all caught up", alarm_at=now.isoformat(timespec="seconds"))
        _log_line(f"{trigger} trigger: nothing due, posted catch-up todo")
        return

    run = start_session(trigger, now=now)
    if run is None:
        _log_line(f"{trigger} trigger: a session was already active, ignored")
        return
    _log_line(f"{trigger} trigger: started session {run.id}")


def fast_tick(client: BeeClient) -> None:
    global _pending_spoken_start
    now = datetime.now(UTC)

    if _pending_spoken_start is not None:
        ids = _pending_spoken_start
        _pending_spoken_start = None
        db = get_session()
        try:
            for uid in ids:
                if db.exec(select(StartPhraseUtterance).where(StartPhraseUtterance.utterance_id == uid)).first() is None:
                    db.add(StartPhraseUtterance(utterance_id=uid))
            db.commit()
        finally:
            db.close()
        _react_to_trigger(client, "spoken", now)

    tick_result = check_and_recreate(client)
    if tick_result is not None and tick_result.get("event") == "tick_detected":
        _react_to_trigger(client, "tick", now)

    advance_result = advance(now=now)
    if advance_result is not None:
        _log_line(f"session advance: {advance_result['event']} (question {advance_result['question_id']})")


def slow_tick(dry_run: bool = False) -> None:
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
            if s.session_type == "lecture_study" and s.approval_status == "approved":
                result = send_post_lecture_sting(now=now)
                if result:
                    _log_line(f"post-lecture sting sent ({result['due_count']} due)")
        except Exception as e:
            _log_line(f"generation FAILED for session {session.id}: {e}")

    if dry_run:
        _log_line("would check stings/cleanup (skipped, dry-run)")
        return

    for event in maybe_send_stings(now=now):
        _log_line(f"sting sent: slot={event['slot']} due_count={event['due_count']}")

    count = cleanup_old_feedback_todos(now=now)
    if count:
        _log_line(f"cleaned up {count} feedback todo(s) older than 12h")
    sting_count = cleanup_old_stings(now=now)
    if sting_count:
        _log_line(f"cleaned up {sting_count} sting todo(s) older than 12h")


def run_once(dry_run: bool = False) -> None:
    """Single-cycle entry point (`--once`): runs one slow tick plus one
    fast tick's worth of ready-todo/session-advance work."""
    slow_tick(dry_run=dry_run)
    if not dry_run:
        with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
            fast_tick(client)


async def run_daemon(dry_run: bool = False) -> None:
    _log_line(f"recall.run starting (dry_run={dry_run})")
    orphans = find_bee_processes()
    if orphans:
        _log_line(f"WARNING: {len(orphans)} pre-existing bee process(es) found before starting: {[o['pid'] for o in orphans]}")

    async def fast_loop():
        last_tick = datetime.now(UTC)
        with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
            while True:
                now = datetime.now(UTC)
                gap = (now - last_tick).total_seconds()
                if gap > SLEEP_GAP_THRESHOLD_S:
                    _log_line(f"sleep/wake gap detected ({gap:.0f}s since last tick) -- recovering in-flight state")
                    result = recover(now=now)
                    if result is not None:
                        _log_line(f"recovered: {result['event']} (question {result['question_id']})")
                last_tick = now
                if not dry_run:
                    try:
                        await asyncio.get_running_loop().run_in_executor(None, fast_tick, client)
                    except Exception as e:
                        _log_line(f"fast_tick FAILED: {e}")
                await asyncio.sleep(FAST_LOOP_INTERVAL_S)

    async def slow_loop():
        while True:
            try:
                await asyncio.get_running_loop().run_in_executor(None, slow_tick, dry_run)
            except Exception as e:
                _log_line(f"slow_tick FAILED: {e}")
            await asyncio.sleep(SLOW_LOOP_INTERVAL_S)

    # consume_stream_live() (recall.ingest) already tree-kills its own bee
    # stream subprocess in a finally block, including under cancellation.
    await asyncio.gather(consume_stream_live(on_new_utterance=_on_new_utterance), fast_loop(), slow_loop())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="do everything except POST/DELETE todos and LLM calls")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit (no live stream)")
    args = parser.parse_args()

    create_db_and_tables()

    if args.once:
        run_once(dry_run=args.dry_run)
        return

    try:
        asyncio.run(run_daemon(dry_run=args.dry_run))
    except KeyboardInterrupt:
        _log_line("Ctrl-C received, shutting down...")


if __name__ == "__main__":
    main()
