"""recall/session.py: v4 mini-session state machine (Phase 5C, AGENT.md).

A mini-session is started by the user (tick todo or spoken start phrase)
and runs due questions back-to-back: question todo -> answer window ->
grade -> delete question todo -> feedback todo -> ~10s pause -> next
question. Ends when the daily quota is hit, nothing is left due, or one
no_answer ends it early (no penalty -- the question stays due for later).
Only one SessionRun is ever `status == "active"` at a time, and at most
one Dispatch is ever open at a time -- the next question is sent only
after the current one is graded (Phase 5G, AGENT.md 2026-10-07). All
state lives in SessionRun + Dispatch rows, so a daemon restart resumes a
mid-session correctly instead of losing it.

Copy rule (AGENT.md, 2026-10-01): Bee rewrites todo text into its own
conversational voice before display -- numbers/facts survive, emoji and
symbols do not. All copy here is plain words: no emoji, no checkmarks,
no "X/Y" shorthand.

"Correct" in the report/goal semantics means "answered" (any verdict
other than no_answer) -- a deliberate interpretation, not literally
verdict == "correct" only, since partial/incorrect answers are still real
engagement and count toward the daily goal and the report's "X out of Y".
"""

import logging
import time
from datetime import UTC, datetime, timedelta

from sqlmodel import select

from recall.bee.client import BeeClient
from recall.bee.process import BeeProxy
from recall.config import settings
from recall.db import Answer, Dispatch, FeedbackTodo, Question, ReviewState
from recall.db import Session as SessionModel
from recall.db import SessionRun, Utterance, get_session
from recall.grading import grade_match
from recall.llm.base import GradeResult
from recall.matcher import MatchResult
from recall.matcher import Utterance as MatcherUtterance
from recall.matcher import match_answer_window
from recall.scoring import current_streak_days, session_run_breakdown
from recall.srs import sm2

logger = logging.getLogger("recall.session")

POST_FEEDBACK_PAUSE_S = 10

# Phase 5G (AGENT.md, 2026-10-07, replaces the Phase 5E/5F "defer at 2 min
# then live-reassign" model after it was found live to loop forever --
# recall/FRICTION_LOG.md, 2-day check Incident 3): no unclaimed speech with
# spoken_at >= alarm_at by alarm_at + 4 min means no_answer, no SM-2
# change, and the session ends cleanly (the rest of the due questions stay
# due). The 4-min figure (not 2) comes from Phase 5D: real answers have
# been measured taking several minutes to become transcribable.
NO_ANSWER_TIMEOUT_S = 240.0

# Phase 5F answer-done rule (AGENT.md, revised 2026-10-05 after a Phase 5E
# live test found a cross-question content bleed), UNCHANGED by Phase 5G:
# an answer with speech in it is "done" only once BOTH no new utterance has
# ARRIVED for ANSWER_QUIET_S AND the last utterance's own spoken_at is at
# least MIN_SPOKEN_AGE_S old. The arrival-only version of this rule let a
# restated-question-then-pause answer get graded on just the restatement,
# before the real explanation (delayed by realtime-transcript lag, Phase
# 5D: 25-39s+) had even arrived.
ANSWER_QUIET_S = 15.0
MIN_SPOKEN_AGE_S = 40.0

# Phase 5G Step 2 (AGENT.md, 2026-10-07): an utterance attaches to at most
# ONE Answer, ever (Utterance.claimed_by_dispatch_id), so claimed content
# can never be re-matched into a later window -- the structural bug behind
# Incident 3. A dispatch is only ever (re)graded while its utterance set
# has actually changed since its last grade; a hard cap on how many times
# that can happen stops any future bug of this shape from burning the LLM
# budget in a loop instead of just failing loudly.
MAX_GRADE_CALLS_PER_DISPATCH = 3

# Phase 5F hint prefix (AGENT.md, revised 2026-10-05): always the short
# prefix; no prefix at all if it still doesn't fit TODO_TEXT_MAX_CHARS
# (Bee's own todo-text cap, a platform constraint distinct from the
# ~95-char question-generation length cap in recall/questions.py).
QUESTION_PREFIX_SHORT = "Recall quiz, no hints: "
TODO_TEXT_MAX_CHARS = 120

# Phase 5F report wording (AGENT.md, revised 2026-10-05): "solid" means
# quality >= 4 only -- SOLID_QUALITY_THRESHOLD lives in recall.scoring
# (session_run_breakdown) since the dashboard needs the same number.


def question_todo_text(question_text: str) -> str:
    short = f"{QUESTION_PREFIX_SHORT}{question_text}"
    if len(short) <= TODO_TEXT_MAX_CHARS:
        return short
    return question_text  # still too long even with the short prefix -- send bare rather than truncate meaning


def _is_approved(db, question: Question) -> bool:
    if question.session_id is None:
        return False
    session = db.get(SessionModel, question.session_id)
    return session is not None and session.approval_status == "approved"


def due_questions(db, now: datetime) -> list[Question]:
    """Due questions ordered by SM-2 due date, then never-dispatched
    (new) questions, approved + active only. Matches AGENT.md Step 3."""
    due: list[Question] = []
    seen_ids: set[int] = set()

    review_states = db.exec(select(ReviewState).where(ReviewState.due_at <= now).order_by(ReviewState.due_at)).all()
    for rs in review_states:
        q = db.get(Question, rs.question_id)
        if q is not None and q.active and q.id not in seen_ids and _is_approved(db, q):
            due.append(q)
            seen_ids.add(q.id)

    already_dispatched_ids = {d.question_id for d in db.exec(select(Dispatch)).all()}
    new_questions = db.exec(select(Question).where(Question.active == True).order_by(Question.id)).all()  # noqa: E712
    for q in new_questions:
        if q.id not in seen_ids and q.id not in already_dispatched_ids and _is_approved(db, q):
            due.append(q)
            seen_ids.add(q.id)

    return due


def has_active_session(db) -> SessionRun | None:
    return db.exec(select(SessionRun).where(SessionRun.status == "active")).first()


def open_dispatch_for(db, session_run_id: int) -> Dispatch | None:
    dispatches = db.exec(
        select(Dispatch).where(Dispatch.session_run_id == session_run_id).where(Dispatch.status == "pending")
    ).all()
    for d in dispatches:
        if db.exec(select(Answer).where(Answer.dispatch_id == d.id)).first() is None:
            return d
    return None


def _feedback_text(question: Question, answer: Answer) -> str:
    """Plain words (AGENT.md copy rule) -- Bee rewrites symbols/emoji away
    before the wearer ever sees them, so there's no point writing any."""
    total = len(question.rubric) or 1
    hit = len(answer.rubric_points_hit)
    if answer.verdict in ("correct", "partial"):
        missed = [r for r in question.rubric if r not in answer.rubric_points_hit]
        if missed:
            return f"Recall: {hit} out of {total}, you missed {missed[0]}"
        return f"Recall: {hit} out of {total}, nicely done"
    if answer.verdict == "no_answer":
        return "Recall: no answer captured, no penalty, it's still due"
    if answer.verdict == "incomplete_capture":
        return f"Recall: {answer.feedback}"
    return f"Recall: not quite, {answer.feedback}"


def _apply_scheduling(db, question: Question, answer: Answer, now: datetime) -> None:
    review_state = db.exec(select(ReviewState).where(ReviewState.question_id == question.id)).first()

    if answer.verdict in ("no_answer", "incomplete_capture"):
        # no_answer and incomplete_capture never touch SM-2 (AGENT.md).
        if answer.verdict == "incomplete_capture" and review_state is not None:
            review_state.due_at = now + timedelta(hours=2)
            db.add(review_state)
        return

    if review_state is None:
        review_state = ReviewState(question_id=question.id)
    result = sm2(answer.quality, review_state.ease_factor, review_state.interval_days, review_state.repetitions)
    review_state.ease_factor = result.ease_factor
    review_state.interval_days = result.interval_days
    review_state.repetitions = result.repetitions
    review_state.due_at = now + timedelta(days=result.interval_days)
    review_state.last_reviewed_at = now
    db.add(review_state)


def _post_todo_tracked(client: BeeClient, text: str) -> int:
    resp = client.create_todo(text, alarm_at=datetime.now(UTC).isoformat(timespec="seconds"))
    return resp["id"]


def _dispatch_question(db, client: BeeClient, session_run: SessionRun, question: Question, now: datetime) -> Dispatch:
    text = question_todo_text(question.text)
    resp = client.create_todo(text, alarm_at=now.isoformat(timespec="seconds"))
    dispatch = Dispatch(
        question_id=question.id,
        bee_todo_id=resp["id"],
        alarm_at=now,
        status="pending",
        dispatched_at=now,
        session_run_id=session_run.id,
    )
    db.add(dispatch)
    session_run.questions_asked += 1
    db.add(session_run)
    db.commit()
    db.refresh(dispatch)
    return dispatch


def _end_session(db, client: BeeClient, session_run: SessionRun, status: str, now: datetime) -> None:
    session_run.status = status
    session_run.ended_at = now
    db.add(session_run)
    db.commit()

    breakdown = session_run_breakdown(db, session_run.id)
    streak = current_streak_days(now)
    report_text = (
        f"Recall: {breakdown['answered']} answered, {breakdown['solid']} solid, "
        f"{breakdown['needs_another_look']} need another look, {streak}-day streak"
    )
    todo_id = _post_todo_tracked(client, report_text)
    db.add(FeedbackTodo(bee_todo_id=todo_id))
    db.commit()


def _claim_utterances(db, dispatch: Dispatch, match: MatchResult) -> None:
    """Phase 5G Step 2: once an utterance is part of a graded Answer, it
    can never be matched into any other window again -- the structural fix
    for Incident 3 (a dispatch kept re-matching the same already-used
    utterances forever). Only claims rows not already claimed by this same
    dispatch (idempotent on a regrade of the same dispatch)."""
    for u in match.utterances:
        row = db.get(Utterance, u.id)
        if row is not None and row.claimed_by_dispatch_id is None:
            row.claimed_by_dispatch_id = dispatch.id
            db.add(row)
    db.commit()


def _persist_answer(db, dispatch: Dispatch, result: GradeResult, match: MatchResult, now: datetime) -> Answer:
    existing = db.exec(select(Answer).where(Answer.dispatch_id == dispatch.id)).first()
    if existing is not None:
        existing.utterance_ids = [u.id for u in match.utterances]
        existing.raw_text = match.raw_text
        existing.verdict = result.verdict
        existing.rubric_points_hit = result.rubric_points_hit
        existing.quality = result.quality
        existing.feedback = result.feedback
        existing.matched_at = now
        db.add(existing)
        db.commit()
        db.refresh(existing)
        answer = existing
    else:
        answer = Answer(
            dispatch_id=dispatch.id,
            utterance_ids=[u.id for u in match.utterances],
            raw_text=match.raw_text,
            verdict=result.verdict,
            rubric_points_hit=result.rubric_points_hit,
            quality=result.quality,
            feedback=result.feedback,
            matched_at=now,
        )
        db.add(answer)
        db.commit()
        db.refresh(answer)

    _claim_utterances(db, dispatch, match)
    return answer


def _finish_grading(db, client: BeeClient, dispatch: Dispatch, question: Question, answer: Answer, now: datetime) -> None:
    dispatch.status = "answered"
    db.add(dispatch)
    _apply_scheduling(db, question, answer, now)
    db.commit()

    # Hard guard: this id came from Dispatch.bee_todo_id, recorded by us.
    client.delete_todo(dispatch.bee_todo_id)
    feedback_text = _feedback_text(question, answer)
    todo_id = _post_todo_tracked(client, feedback_text)
    db.add(FeedbackTodo(bee_todo_id=todo_id))

    session_run = db.get(SessionRun, dispatch.session_run_id)
    if answer.verdict != "no_answer":
        session_run.questions_answered += 1
        db.add(session_run)
    db.commit()


def _previous_dispatch(db, session_run: SessionRun, current: Dispatch) -> Dispatch | None:
    dispatches = db.exec(
        select(Dispatch)
        .where(Dispatch.session_run_id == session_run.id)
        .where(Dispatch.id < current.id)
        .order_by(Dispatch.id.desc())
    ).all()
    return dispatches[0] if dispatches else None


def _grade_with_guards(db, dispatch: Dispatch, question: Question, previous_question: Question | None, match: MatchResult) -> GradeResult | None:
    """Phase 5G Step 2: calls the grader only when this dispatch's
    utterance set has actually changed since its last grade, and never
    more than MAX_GRADE_CALLS_PER_DISPATCH times -- on the (would-be) 4th
    call, stops, marks the dispatch `needs_review`, and logs it instead of
    calling the LLM again. Returns None when the call was skipped for
    either reason. `previous_question` is passed through to grade_match
    purely so the grader's current|previous|neither field (logged as a
    diagnostic only -- it never moves data in Phase 5G) has something to
    compare against; the result is always persisted against THIS dispatch
    regardless of what it says."""
    existing = db.exec(select(Answer).where(Answer.dispatch_id == dispatch.id)).first()
    new_ids = sorted(u.id for u in match.utterances)
    if existing is not None and sorted(existing.utterance_ids) == new_ids:
        return None  # nothing changed since the last grade -- skip

    if dispatch.grade_call_count >= MAX_GRADE_CALLS_PER_DISPATCH:
        dispatch.status = "needs_review"
        db.add(dispatch)
        db.commit()
        logger.warning(
            "dispatch %d (question %d) hit %d grade calls without stabilizing -- marking needs_review, not grading again",
            dispatch.id, question.id, MAX_GRADE_CALLS_PER_DISPATCH,
        )
        return None

    dispatch.grade_call_count += 1
    db.add(dispatch)
    db.commit()

    result = grade_match(question, match, previous_question=previous_question)
    if result.answers != "current":
        logger.info(
            "dispatch %d (question %d): grader diagnostic answers=%s (logged only, never moves data)",
            dispatch.id, question.id, result.answers,
        )
    return result


def _grade_dispatch(db, client: BeeClient, dispatch: Dispatch, question: Question, now: datetime, match: MatchResult) -> Answer | None:
    session_run = db.get(SessionRun, dispatch.session_run_id)
    previous = _previous_dispatch(db, session_run, dispatch)
    previous_question = db.get(Question, previous.question_id) if previous is not None else None

    result = _grade_with_guards(db, dispatch, question, previous_question, match)
    if result is None:
        return None

    answer = _persist_answer(db, dispatch, result, match, now)
    _finish_grading(db, client, dispatch, question, answer, now)
    return answer


def _grade_no_answer(db, client: BeeClient, dispatch: Dispatch, question: Question, now: datetime) -> Answer:
    """No speech at all by alarm_at + NO_ANSWER_TIMEOUT_S: graded directly,
    no LLM call and no grade-call-count guard involved (grade_match()
    already short-circuits an empty window the same way)."""
    empty_match = MatchResult(utterances=[], raw_text="", trigger_found=False)
    result = grade_match(question, empty_match)
    answer = _persist_answer(db, dispatch, result, empty_match, now)
    _finish_grading(db, client, dispatch, question, answer, now)
    return answer


def start_session(trigger: str, now: datetime | None = None) -> SessionRun | None:
    """Starts a mini-session if none is active and questions are due.
    Returns None (caller should post the "nothing due" todo) if nothing
    is due, or if a session is already active."""
    now = now or datetime.now(UTC)
    db = get_session()
    try:
        if has_active_session(db) is not None:
            return None

        due = due_questions(db, now)
        if not due:
            return None

        session_run = SessionRun(trigger=trigger, status="active", started_at=now)
        db.add(session_run)
        db.commit()
        db.refresh(session_run)

        with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
            _dispatch_question(db, client, session_run, due[0], now)

        db.refresh(session_run)
        return session_run
    finally:
        db.close()


def _classify_window(db, dispatch: Dispatch, now: datetime) -> tuple[str, MatchResult]:
    """Classifies an open dispatch's answer window as 'wait' | 'ready' |
    'no_answer' (AGENT.md answer-done rule, Phase 5G): ready only once
    BOTH no new utterance has ARRIVED for ANSWER_QUIET_S AND the last
    utterance's own spoken_at is at least MIN_SPOKEN_AGE_S old (or the
    4-min hard cap force-closes a still-ongoing answer regardless);
    no_answer if no speech at all arrives by alarm_at + 4 min.

    Window queries exclude already-claimed utterances (Phase 5G Step 2) --
    once an utterance has fed some Answer, it can never be matched into
    another window. Arrival is tracked via
    Dispatch.last_seen_utterance_id/_at rather than in-memory, so it
    survives a daemon restart. tail_close_s is set to the hard cap itself
    (effectively disabling the matcher's own internal pruning) -- within
    the window everything accumulates regardless of internal gaps; this
    function's own arrival/spoken-age logic decides readiness, not the
    matcher's."""
    rows = db.exec(
        select(Utterance)
        .where(Utterance.spoken_at >= dispatch.alarm_at)
        .where(Utterance.claimed_by_dispatch_id.is_(None))
        .order_by(Utterance.spoken_at)
    ).all()
    utterances = [MatcherUtterance(id=r.id, text=r.text, spoken_at=r.spoken_at) for r in rows]
    match = match_answer_window(utterances, alarm_at=dispatch.alarm_at, tail_close_s=NO_ANSWER_TIMEOUT_S, hard_cap_s=NO_ANSWER_TIMEOUT_S)
    hard_cap_at = dispatch.alarm_at + timedelta(seconds=NO_ANSWER_TIMEOUT_S)

    if match.utterances:
        last = match.utterances[-1]
        if dispatch.last_seen_utterance_id != last.id:
            dispatch.last_seen_utterance_id = last.id
            dispatch.last_seen_utterance_at = now
            db.add(dispatch)
            db.commit()
        arrived_quiet_for = (now - dispatch.last_seen_utterance_at).total_seconds()
        spoken_quiet_for = (now - last.spoken_at).total_seconds()
        if (arrived_quiet_for >= ANSWER_QUIET_S and spoken_quiet_for >= MIN_SPOKEN_AGE_S) or now >= hard_cap_at:
            return "ready", match
        return "wait", match
    if now >= hard_cap_at:
        return "no_answer", match
    return "wait", match


def advance(now: datetime | None = None) -> dict | None:
    """Called on every daemon poll tick. Classifies the active session's
    open dispatch as wait/ready/no_answer and acts accordingly, then
    either ends the session or dispatches the next due question. Returns a
    dict describing what happened, or None if there's nothing to do."""
    now = now or datetime.now(UTC)
    db = get_session()
    try:
        session_run = has_active_session(db)
        if session_run is None:
            return None

        dispatch = open_dispatch_for(db, session_run.id)
        if dispatch is None:
            return None  # shouldn't happen -- an active session always has one open dispatch

        status, match = _classify_window(db, dispatch, now)
        if status == "wait":
            return None

        question = db.get(Question, dispatch.question_id)
        with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
            if status == "no_answer":
                _grade_no_answer(db, client, dispatch, question, now)
                db.refresh(session_run)
                _end_session(db, client, session_run, "ended_no_answer", now)
                return {"event": "session_ended_no_answer", "question_id": question.id}

            answer = _grade_dispatch(db, client, dispatch, question, now, match)
            if answer is None:
                # Grade skipped (unchanged utterance set, or the grade-call
                # cap was hit and the dispatch was marked needs_review) --
                # nothing to act on this tick; try again later.
                return None
            time.sleep(POST_FEEDBACK_PAUSE_S)
            db.refresh(session_run)

            if session_run.questions_asked >= settings.daily_quota:
                _end_session(db, client, session_run, "completed", now)
                return {"event": "session_completed_quota", "question_id": question.id}

            already_asked_ids = {
                d.question_id for d in db.exec(select(Dispatch).where(Dispatch.session_run_id == session_run.id)).all()
            }
            remaining = [q for q in due_questions(db, now) if q.id not in already_asked_ids]
            if not remaining:
                _end_session(db, client, session_run, "completed", now)
                return {"event": "session_completed_exhausted", "question_id": question.id}

            next_question = remaining[0]
            _dispatch_question(db, client, session_run, next_question, now)
            return {"event": "next_question_dispatched", "question_id": next_question.id}
    finally:
        db.close()


def recover(now: datetime | None = None) -> dict | None:
    """Phase 5G: wake/reconnect recovery is just a normal advance() call --
    _classify_window is driven entirely by the explicit `now` passed in,
    not by how continuously it's been polled, so there's nothing special
    left to do here beyond giving the open dispatch a chance to resolve
    immediately on wake rather than waiting for the next regular fast
    tick. Superseded the old Phase 5E behavior of forcing a defer+pause on
    a stale dispatch -- that whole state no longer exists."""
    return advance(now=now)
