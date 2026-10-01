"""recall/session.py: v4 mini-session state machine (Phase 5C, AGENT.md).

A mini-session is started by the user (tick todo or spoken start phrase)
and runs due questions back-to-back: question todo -> answer window ->
grade -> delete question todo -> feedback todo -> ~10s pause -> next
question. Ends when the daily quota is hit, nothing is left due, or one
no_answer ends it early (no penalty -- the question stays due for later).
Only one SessionRun is ever `status == "active"` at a time. All state
lives in SessionRun + Dispatch rows, so a daemon restart resumes a
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

import time
from datetime import UTC, datetime, timedelta

from sqlmodel import select

from recall.bee.client import BeeClient
from recall.bee.process import BeeProxy
from recall.config import settings
from recall.db import Answer, Dispatch, FeedbackTodo, Question, ReviewState
from recall.db import Session as SessionModel
from recall.db import SessionRun, Utterance, get_session
from recall.grading import grade_and_persist
from recall.matcher import DEFAULT_HARD_CAP_S
from recall.matcher import Utterance as MatcherUtterance
from recall.matcher import match_answer_window
from recall.scoring import current_streak_days
from recall.srs import sm2

ANSWER_WINDOW_SAFETY_BUFFER_S = 30  # on top of the matcher's own hard cap
POST_FEEDBACK_PAUSE_S = 10


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
    text = f"Recall: {question.text}"
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

    streak = current_streak_days(now)
    report_text = f"Recall: {session_run.questions_answered} out of {session_run.questions_asked} correct today"
    if streak > 0:
        report_text += f", {streak} day streak"
    todo_id = _post_todo_tracked(client, report_text)
    db.add(FeedbackTodo(bee_todo_id=todo_id))
    db.commit()


def _grade_dispatch(db, client: BeeClient, dispatch: Dispatch, question: Question, now: datetime, force_no_answer: bool = False) -> Answer:
    """Grades one in-session dispatch, deletes its question todo, posts
    feedback. `force_no_answer` short-circuits matching/grading entirely
    (used by recover() on wake -- too much time has passed to trust any
    late-arriving utterances as a real answer to this specific question)."""
    if force_no_answer:
        from recall.grading import NO_ANSWER_FEEDBACK
        from recall.llm.base import GradeResult

        result = GradeResult(verdict="no_answer", rubric_points_hit=[], quality=0, feedback=NO_ANSWER_FEEDBACK)
        answer = Answer(
            dispatch_id=dispatch.id,
            utterance_ids=[],
            raw_text="",
            verdict=result.verdict,
            rubric_points_hit=result.rubric_points_hit,
            quality=result.quality,
            feedback=result.feedback,
        )
        db.add(answer)
        db.commit()
        db.refresh(answer)
    else:
        rows = db.exec(select(Utterance).where(Utterance.spoken_at >= dispatch.alarm_at).order_by(Utterance.spoken_at)).all()
        utterances = [MatcherUtterance(id=r.id, text=r.text, spoken_at=r.spoken_at) for r in rows]
        match = match_answer_window(utterances, alarm_at=dispatch.alarm_at)
        answer = grade_and_persist(dispatch, question, match)

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


def advance(now: datetime | None = None) -> dict | None:
    """Called on every daemon poll tick. If the active session's open
    dispatch's answer window has closed, grades it and either ends the
    session or dispatches the next due question. Returns a dict
    describing what happened, or None if there's nothing to do."""
    now = now or datetime.now(UTC)
    db = get_session()
    try:
        session_run = has_active_session(db)
        if session_run is None:
            return None

        dispatch = open_dispatch_for(db, session_run.id)
        if dispatch is None:
            return None  # shouldn't happen -- an active session always has one open dispatch

        window_closed_at = dispatch.alarm_at + timedelta(seconds=DEFAULT_HARD_CAP_S + ANSWER_WINDOW_SAFETY_BUFFER_S)
        if now < window_closed_at:
            return None

        question = db.get(Question, dispatch.question_id)
        with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
            answer = _grade_dispatch(db, client, dispatch, question, now)

            if answer.verdict == "no_answer":
                _end_session(db, client, session_run, "ended_no_answer", now)
                return {"event": "session_ended_no_answer", "question_id": question.id}

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
    """Phase 5C Step 0.3: wake/reconnect recovery. If there's an active
    session with an open dispatch whose answer window has already passed,
    force it directly to no_answer (no penalty) and end the session
    cleanly -- too much time has elapsed (sleep/disconnect) to trust any
    late-arriving utterance as a real answer to that specific question."""
    now = now or datetime.now(UTC)
    db = get_session()
    try:
        session_run = has_active_session(db)
        if session_run is None:
            return None

        dispatch = open_dispatch_for(db, session_run.id)
        if dispatch is None:
            return None

        window_closed_at = dispatch.alarm_at + timedelta(seconds=DEFAULT_HARD_CAP_S + ANSWER_WINDOW_SAFETY_BUFFER_S)
        if now < window_closed_at:
            return None  # window hasn't even passed yet -- let advance() handle it normally

        question = db.get(Question, dispatch.question_id)
        with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
            _grade_dispatch(db, client, dispatch, question, now, force_no_answer=True)
            _end_session(db, client, session_run, "ended_no_answer", now)
        return {"event": "recovered_stale_session", "question_id": question.id}
    finally:
        db.close()
