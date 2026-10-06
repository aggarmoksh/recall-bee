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
from recall.grading import grade_and_persist, grade_match
from recall.llm.base import GradeResult
from recall.matcher import MatchResult
from recall.matcher import Utterance as MatcherUtterance
from recall.matcher import fuzzy_contains_trigger, match_answer_window
from recall.scoring import current_streak_days, replay_review_state, session_run_breakdown
from recall.srs import sm2

POST_FEEDBACK_PAUSE_S = 10

# Phase 5E Step 3 session pacing (AGENT.md, 2026-10-05): if NO speech at all
# arrives within 2 min, the dispatch is deferred (not graded) rather than
# forced to no_answer -- Phase 5D measured real answers sometimes taking
# several minutes to become transcribable, so an immediate no_answer here
# would risk discarding a genuine answer, not just a real miss.
NO_SPEECH_DEFER_S = 120.0
PENDING_GRADE_FALLBACK_WINDOW_S = 600.0  # used only when no later dispatch exists yet to bound the window

# Phase 5F answer-done rule (AGENT.md, revised 2026-10-05 after a Phase 5E
# live test found a cross-question content bleed): an answer with speech in
# it is "done" only once BOTH no new utterance has ARRIVED for
# ANSWER_QUIET_S AND the last utterance's own spoken_at is at least
# MIN_SPOKEN_AGE_S old. The arrival-only version of this rule let a
# restated-question-then-pause answer get graded on just the restatement,
# before the real explanation (delayed by realtime-transcript lag, Phase
# 5D: 25-39s+) had even arrived.
ANSWER_QUIET_S = 15.0
MIN_SPOKEN_AGE_S = 40.0

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


def _persist_answer(db, dispatch: Dispatch, result: GradeResult, match: MatchResult, now: datetime) -> Answer:
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


def _merged_match(db, previous_answer: Answer | None, current_match: MatchResult) -> MatchResult:
    """Combines whatever the previous dispatch's existing answer already
    has (if any) with the current match's utterances, in chronological
    order -- used when a late-arriving answer to the previous question got
    captured in the current question's window instead (Phase 5F)."""
    combined: list[MatcherUtterance] = []
    if previous_answer is not None:
        for uid in previous_answer.utterance_ids:
            u = db.get(Utterance, uid)
            if u is not None:
                combined.append(MatcherUtterance(id=u.id, text=u.text, spoken_at=u.spoken_at))
    combined.extend(current_match.utterances)
    combined.sort(key=lambda u: u.spoken_at)
    raw_text = " ".join(u.text for u in combined)
    trigger_found = any(fuzzy_contains_trigger(u.text) for u in combined)
    return MatchResult(utterances=combined, raw_text=raw_text, trigger_found=trigger_found)


def _reassign_to_previous(
    db, client: BeeClient, previous: Dispatch, previous_question: Question, current_match: MatchResult, now: datetime
) -> Answer:
    """Phase 5F: the grader decided speech captured in the CURRENT
    dispatch's window actually answers the PREVIOUS one instead. Merges it
    with whatever that dispatch's answer already has (none, if it was
    still 'processing') and regrades once, replacing -- not duplicating --
    its Answer row. Then replays SM-2 for that question from scratch
    (recall.scoring.replay_review_state), since the prior grade may
    already have fed SM-2 once under a now-corrected verdict, and a single
    incremental 'undo' isn't reliable (same reasoning as the Phase 5C
    no_answer recompute). No new feedback todo -- the correction shows up
    on the dashboard only, consistent with deferred-grade corrections."""
    old_answer = db.exec(select(Answer).where(Answer.dispatch_id == previous.id)).first()
    old_verdict = old_answer.verdict if old_answer is not None else None
    merged = _merged_match(db, old_answer, current_match)

    result = grade_match(previous_question, merged)  # plain regrade -- no further previous-of-previous lookback

    if old_answer is not None:
        old_answer.utterance_ids = [u.id for u in merged.utterances]
        old_answer.raw_text = merged.raw_text
        old_answer.verdict = result.verdict
        old_answer.rubric_points_hit = result.rubric_points_hit
        old_answer.quality = result.quality
        old_answer.feedback = result.feedback
        old_answer.matched_at = now
        db.add(old_answer)
        answer = old_answer
    else:
        answer = Answer(
            dispatch_id=previous.id,
            utterance_ids=[u.id for u in merged.utterances],
            raw_text=merged.raw_text,
            verdict=result.verdict,
            rubric_points_hit=result.rubric_points_hit,
            quality=result.quality,
            feedback=result.feedback,
            matched_at=now,
        )
        db.add(answer)

    previous.status = "answered"
    db.add(previous)
    db.commit()
    db.refresh(answer)

    replay_review_state(db, previous_question.id, now)

    session_run = db.get(SessionRun, previous.session_run_id)
    if session_run is not None:
        old_counted = old_verdict is not None and old_verdict != "no_answer"
        new_counted = answer.verdict != "no_answer"
        if new_counted and not old_counted:
            session_run.questions_answered += 1
            db.add(session_run)
            db.commit()
        elif old_counted and not new_counted:
            session_run.questions_answered -= 1
            db.add(session_run)
            db.commit()

    return answer


def _grade_dispatch(db, client: BeeClient, dispatch: Dispatch, question: Question, now: datetime, match: MatchResult) -> dict:
    """Grades the current dispatch -- UNLESS the grader decides this
    speech actually answers the PREVIOUS question (a late-arriving answer
    whose own window closed before it finished transcribing, Phase 5F), in
    which case it's reassigned there instead and the current dispatch is
    left exactly as if nothing had arrived for it yet (no Answer written,
    still 'pending'). Returns {'outcome': 'graded', 'answer': Answer} or
    {'outcome': 'reassigned', 'previous_question_id': int}."""
    session_run = db.get(SessionRun, dispatch.session_run_id)
    previous = _previous_dispatch(db, session_run, dispatch)
    previous_question = db.get(Question, previous.question_id) if previous is not None else None

    result = grade_match(question, match, previous_question=previous_question)

    if result.answers == "previous" and previous is not None:
        _reassign_to_previous(db, client, previous, previous_question, match, now)
        return {"outcome": "reassigned", "previous_question_id": previous_question.id}

    answer = _persist_answer(db, dispatch, result, match, now)
    _finish_grading(db, client, dispatch, question, answer, now)
    return {"outcome": "graded", "answer": answer}


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
    'defer' (AGENT.md answer-done rule, revised Phase 5F): ready only once
    BOTH no new utterance has ARRIVED for ANSWER_QUIET_S AND the last
    utterance's own spoken_at is at least MIN_SPOKEN_AGE_S old (or the
    2-min hard cap force-closes a still-ongoing answer regardless); defer
    (no grading yet) if no speech at all arrives by alarm_at + 2 min.

    Arrival is tracked via Dispatch.last_seen_utterance_id/_at rather than
    in-memory, so it survives a daemon restart. tail_close_s is set to the
    hard cap itself (effectively disabling the matcher's own internal
    pruning) -- within the 2-min window everything accumulates regardless
    of internal gaps; this function's own arrival/spoken-age logic decides
    readiness, not the matcher's."""
    rows = db.exec(select(Utterance).where(Utterance.spoken_at >= dispatch.alarm_at).order_by(Utterance.spoken_at)).all()
    utterances = [MatcherUtterance(id=r.id, text=r.text, spoken_at=r.spoken_at) for r in rows]
    match = match_answer_window(utterances, alarm_at=dispatch.alarm_at, tail_close_s=NO_SPEECH_DEFER_S, hard_cap_s=NO_SPEECH_DEFER_S)
    hard_cap_at = dispatch.alarm_at + timedelta(seconds=NO_SPEECH_DEFER_S)

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
        return "defer", match
    return "wait", match


def _defer_dispatch(db, client: BeeClient, dispatch: Dispatch) -> None:
    """No speech by alarm_at + 2 min: move on without grading. Graded
    later in the background once the final transcript becomes available
    (grade_pending_dispatches) -- never blocks the session."""
    dispatch.status = "processing"
    db.add(dispatch)
    db.commit()
    client.delete_todo(dispatch.bee_todo_id)


def _last_two_dispatches_both_deferred(db, session_run: SessionRun) -> bool:
    dispatches = db.exec(select(Dispatch).where(Dispatch.session_run_id == session_run.id).order_by(Dispatch.id)).all()
    if len(dispatches) < 2:
        return False
    return dispatches[-1].status == "processing" and dispatches[-2].status == "processing"


def advance(now: datetime | None = None) -> dict | None:
    """Called on every daemon poll tick. Classifies the active session's
    open dispatch as wait/ready/defer and acts accordingly, then either
    ends the session or dispatches the next due question. Returns a dict
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

        status, match = _classify_window(db, dispatch, now)
        if status == "wait":
            return None

        question = db.get(Question, dispatch.question_id)
        with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
            if status == "defer":
                _defer_dispatch(db, client, dispatch)
                db.refresh(session_run)
                if _last_two_dispatches_both_deferred(db, session_run):
                    _end_session(db, client, session_run, "paused", now)
                    return {"event": "session_paused_two_deferred", "question_id": question.id}
            else:
                grade_result = _grade_dispatch(db, client, dispatch, question, now, match)
                if grade_result["outcome"] == "reassigned":
                    # Current dispatch stays open (no Answer written, still
                    # 'pending') -- never dispatch the next question until
                    # it's graded or pending (AGENT.md answer-done rule).
                    db.refresh(session_run)
                    return {
                        "event": "reassigned_to_previous",
                        "question_id": question.id,
                        "previous_question_id": grade_result["previous_question_id"],
                    }
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
            event = "next_question_dispatched" if status != "defer" else "deferred_next_question_dispatched"
            return {"event": event, "question_id": next_question.id}
    finally:
        db.close()


def recover(now: datetime | None = None) -> dict | None:
    """Phase 5C Step 0.3, updated Phase 5E: wake/reconnect recovery. If
    there's an active session with an open dispatch whose answer window
    has already passed, defer it (not force no_answer) and end the
    session (status 'paused', no penalty) -- a sleep/wake gap can be
    minutes long, and Phase 5D measured real answers sometimes taking
    several minutes to become transcribable, so forcing no_answer here
    would risk discarding a genuine answer the final transcript just
    hasn't caught up with yet. Deferred means grade_pending_dispatches
    picks it up later, same as any other deferred dispatch."""
    now = now or datetime.now(UTC)
    db = get_session()
    try:
        session_run = has_active_session(db)
        if session_run is None:
            return None

        dispatch = open_dispatch_for(db, session_run.id)
        if dispatch is None:
            return None

        hard_cap_at = dispatch.alarm_at + timedelta(seconds=NO_SPEECH_DEFER_S)
        if now < hard_cap_at:
            return None  # window hasn't even passed yet -- let advance() handle it normally

        question = db.get(Question, dispatch.question_id)
        with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
            _defer_dispatch(db, client, dispatch)
            _end_session(db, client, session_run, "paused", now)
        return {"event": "recovered_stale_session", "question_id": question.id}
    finally:
        db.close()


def _next_alarm_after(db, alarm_at: datetime) -> datetime | None:
    rows = db.exec(select(Dispatch.alarm_at).where(Dispatch.alarm_at > alarm_at).order_by(Dispatch.alarm_at)).all()
    return rows[0] if rows else None


def grade_pending_dispatches(now: datetime | None = None) -> list[dict]:
    """Phase 5E Step 3: grades dispatches deferred by _defer_dispatch once
    the final (non-realtime) transcript for their conversation window
    becomes available. Matched by spoken_at in [this alarm_at, next
    alarm_at) -- the next dispatch anywhere marks the boundary past which
    speech belongs to a different question, not this one; if none exists
    yet, falls back to a generous fixed window. Never blocks session
    advancement -- called periodically from the slow loop. Posts no
    feedback todo: by the time this runs the session has already moved on
    or ended, so the grade shows up on the dashboard only."""
    now = now or datetime.now(UTC)
    db = get_session()
    results: list[dict] = []
    try:
        pending = db.exec(select(Dispatch).where(Dispatch.status == "processing")).all()
        for dispatch in pending:
            next_alarm = _next_alarm_after(db, dispatch.alarm_at)
            window_end = next_alarm or (dispatch.alarm_at + timedelta(seconds=PENDING_GRADE_FALLBACK_WINDOW_S))
            rows = db.exec(
                select(Utterance)
                .where(Utterance.spoken_at >= dispatch.alarm_at)
                .where(Utterance.spoken_at < window_end)
                .order_by(Utterance.spoken_at)
            ).all()
            if not rows:
                continue  # still nothing yet -- try again on the next pass

            question = db.get(Question, dispatch.question_id)
            utterances = [MatcherUtterance(id=r.id, text=r.text, spoken_at=r.spoken_at) for r in rows]
            span_s = max((window_end - dispatch.alarm_at).total_seconds(), 1.0)
            match = match_answer_window(utterances, alarm_at=dispatch.alarm_at, tail_close_s=span_s, hard_cap_s=span_s)
            if not match.utterances:
                continue

            answer = grade_and_persist(dispatch, question, match)
            dispatch.status = "answered"
            db.add(dispatch)
            _apply_scheduling(db, question, answer, now)

            session_run = db.get(SessionRun, dispatch.session_run_id)
            if session_run is not None and answer.verdict != "no_answer":
                session_run.questions_answered += 1
                db.add(session_run)
            db.commit()
            results.append({"dispatch_id": dispatch.id, "question_id": question.id, "verdict": answer.verdict})
    finally:
        db.close()
    return results
