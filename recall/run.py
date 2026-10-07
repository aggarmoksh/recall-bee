"""Daemon: python -m recall.run

v4 pull-with-nudges model (AGENT.md, Phase 5C/5E). Four concurrent loops:

- the live stream (recall.ingest.consume_stream_live) -- ingests
  utterances in real time, buffers a short rolling window to detect the
  spoken session-start phrase (variant A) as it's said, and immediately
  fetches a conversation's final transcript the instant its own
  update-conversation event reports a no-longer-CAPTURING/PROCESSING state.
- a live poll loop (~5s, recall.ingest.poll_live_conversations): the
  realtime-transcription poller (Phase 5D/5E) that makes the 15s-quiet
  answer-done pacing in recall.session viable -- the old 60s poll_once
  cycle is far too slow to notice speech has stopped within 15s.
- a fast loop (~10s): polls the ready-todo for a tick, reacts to a
  just-detected spoken start, and advances an active mini-session
  (grades a closed answer window, defers a silent one, dispatches the
  next question, or ends/pauses the session) -- kept fast so a session
  feels back-to-back, not laggy.
- a slow loop (~60s): ingest poll, session classification for newly-
  closed sessions (+ a post-lecture sting), time-based stings, old
  feedback/sting-todo cleanup, sleep/wake recovery, and grading any
  dispatches deferred by the session loop once their final transcript
  becomes available.

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
from recall.heartbeat import record_tick
from recall.ingest import consume_stream_live, poll_live_conversations, poll_once
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

# Phase 5G Step 4 (AGENT.md, 2026-10-07): per-loop supervisor backoff, after
# either 2-day-check crash (FRICTION_LOG.md) took down the whole daemon
# process via an exception that escaped asyncio.gather() entirely -- not
# just the one tick it happened on. Each loop below now runs under
# _supervised(), which can never let that happen again: an exception is
# logged and that ONE loop restarts after a capped backoff, the other
# loops keep running untouched.
SUPERVISOR_BACKOFF_STEPS_S = [5.0, 15.0, 60.0]

logger = logging.getLogger("recall.run")

_recent_utterances: deque[tuple[int, str, datetime]] = deque()
_pending_spoken_start: list[int] | None = None  # utterance ids that triggered detection, or None


def _log_line(message: str) -> None:
    """Writes directly to LOG_PATH only -- no print()/stdout duplication.
    Phase 5E Step 6 found a real Windows bug from having both: `print()`
    goes through Python's own block-buffered stdout (not line-buffered,
    since stdout isn't a tty once shell-redirected to a file), held in an
    internal buffer and flushed late to whatever byte offset THAT handle's
    own position tracking thinks is current -- a position that drifts out
    of sync with this function's own `open(path, "a")` writes, since the
    two are independent OS file handles on the same path. The practical
    symptom: old lines reappeared via `tail -f` minutes after being
    written, and a live session-start event was indistinguishable from a
    stale flush of an hours-old one. The `open(..., "a")` write alone is
    already reliable (every "ingest:"/"found pre-existing" line arrived
    immediately and in order) -- dropping the redundant, buggy print()
    path removes the race instead of trying to keep both in sync."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    line = f"{timestamp}  {message}"
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
    """Called synchronously for every newly ingested utterance, from
    whichever path saw it first (stream, 5s live poller, or the 60s
    poll_once fallback -- Phase 5E Step 2). Buffers a short rolling
    window and flags a spoken-start detection for the fast loop to react
    to.

    The window is keyed on ARRIVAL time (when this callback runs), not
    `spoken_at` (when the words were actually said) -- Phase 5E Step 6
    found this the hard way: with realtime-transcription latency
    routinely 25-40s+ (Phase 5D) and no upper bound via the 60s poll_once
    fallback, an utterance keyed on spoken_at could already be older than
    START_PHRASE_WINDOW_S the instant it arrives, pruning it from the
    buffer before detection ever runs on it -- silently killing every
    spoken start whose ingestion took longer than 15s, which is the
    common case now, not an edge case."""
    global _pending_spoken_start
    now = datetime.now(UTC)
    _recent_utterances.append((utterance_id, text, now))
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
        counts = poll_once(client, on_new_utterance=_on_new_utterance)
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


async def _supervised(name: str, coro_fn, *args, **kwargs) -> None:
    """Runs coro_fn(*args, **kwargs) forever. Any exception that escapes
    it is logged and THIS loop alone restarts after a capped backoff
    (SUPERVISOR_BACKOFF_STEPS_S) -- it never propagates out to
    asyncio.gather() and never takes the other loops down with it (Phase
    5G Step 4, AGENT.md 2026-10-07)."""
    backoff_index = 0
    while True:
        try:
            await coro_fn(*args, **kwargs)
            _log_line(f"{name} loop exited normally (unexpected for an infinite loop) -- restarting")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _log_line(f"{name} loop FAILED: {e!r} -- restarting")
        backoff = SUPERVISOR_BACKOFF_STEPS_S[min(backoff_index, len(SUPERVISOR_BACKOFF_STEPS_S) - 1)]
        backoff_index += 1
        await asyncio.sleep(backoff)


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
                record_tick(now=datetime.now(UTC))
                await asyncio.sleep(FAST_LOOP_INTERVAL_S)

    async def slow_loop():
        while True:
            try:
                await asyncio.get_running_loop().run_in_executor(None, slow_tick, dry_run)
            except Exception as e:
                _log_line(f"slow_tick FAILED: {e}")
            await asyncio.sleep(SLOW_LOOP_INTERVAL_S)

    async def live_poll_loop(client: BeeClient):
        await poll_live_conversations(client, on_new_utterance=_on_new_utterance)

    # consume_stream_live() (recall.ingest) already tree-kills its own bee
    # stream subprocess in a finally block, including under cancellation.
    # live_proxy/live_client (Phase 5E Step 1) is a second, long-lived
    # proxy held open for the whole daemon run -- the 5s live poller can't
    # afford to spin up a fresh `bee proxy` subprocess every tick the way
    # fast_tick/slow_tick do for their own occasional calls.
    with BeeProxy(port=settings.bee_proxy_port) as live_proxy, BeeClient(live_proxy.base_url) as live_client:
        await asyncio.gather(
            _supervised("stream", consume_stream_live, on_new_utterance=_on_new_utterance, client=live_client),
            _supervised("fast", fast_loop),
            _supervised("slow", slow_loop),
            _supervised("live_poll", live_poll_loop, live_client),
        )


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
