"""Answer flow: after a dispatch's answer window closes, match + grade the
spoken response, update SM-2 scheduling, delete the question todo, and post
a feedback todo. Also handles cleanup of old feedback todos.

Hard guard (AGENT.md): only ever DELETE todo ids that Recall itself
recorded -- a Dispatch's own bee_todo_id, or a bee_todo_id tracked in the
FeedbackTodo table. Never an arbitrary/external id. The tests in
tests/test_answer_flow.py prove a user's own todo is never touched.
"""

from datetime import UTC, datetime, timedelta

from sqlmodel import select

from recall.bee.client import BeeClient
from recall.bee.process import BeeProxy
from recall.config import settings
from recall.db import Answer, Dispatch, FeedbackTodo, Question, ReviewState, Utterance, get_session
from recall.grading import grade_and_persist
from recall.matcher import MatchResult
from recall.matcher import Utterance as MatcherUtterance
from recall.matcher import match_answer_window
from recall.srs import sm2

FEEDBACK_TODO_MAX_AGE_HOURS = 12
INCOMPLETE_CAPTURE_REDISPATCH_HOURS = 2


def _match_for_dispatch(db, dispatch: Dispatch) -> MatchResult:
    rows = db.exec(select(Utterance).where(Utterance.spoken_at >= dispatch.alarm_at).order_by(Utterance.spoken_at)).all()
    utterances = [MatcherUtterance(id=r.id, text=r.text, spoken_at=r.spoken_at) for r in rows]
    return match_answer_window(utterances, alarm_at=dispatch.alarm_at)


def _feedback_todo_text(answer: Answer, question: Question) -> str:
    if answer.verdict == "incomplete_capture":
        return f"Recall: {answer.feedback}"
    if answer.verdict in ("correct", "partial"):
        total = len(question.rubric) or 1
        hit = len(answer.rubric_points_hit)
        return f"Recall ✓ {hit}/{total}: {answer.feedback}"
    return f"Recall ✗: {answer.feedback}"


def _apply_scheduling(db, question: Question, answer: Answer) -> None:
    now = datetime.now(UTC)
    review_state = db.exec(select(ReviewState).where(ReviewState.question_id == question.id)).first()

    if answer.verdict == "incomplete_capture":
        # No SM-2 update at all -- just re-ask sooner.
        if review_state is not None:
            review_state.due_at = now + timedelta(hours=INCOMPLETE_CAPTURE_REDISPATCH_HOURS)
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


def process_dispatch_answer(dispatch_id: int) -> Answer:
    """Runs the full answer flow for one dispatch: match, grade, schedule,
    delete the question todo, post feedback. Returns the persisted Answer."""
    db = get_session()
    try:
        dispatch = db.get(Dispatch, dispatch_id)
        if dispatch is None:
            raise ValueError(f"no Dispatch with id={dispatch_id}")
        question = db.get(Question, dispatch.question_id)
        match = _match_for_dispatch(db, dispatch)
    finally:
        db.close()

    answer = grade_and_persist(dispatch, question, match)

    db = get_session()
    try:
        dispatch_row = db.get(Dispatch, dispatch_id)
        dispatch_row.status = "answered"
        db.add(dispatch_row)
        _apply_scheduling(db, question, answer)
        db.commit()
    finally:
        db.close()

    with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
        # Hard guard: this id came from Dispatch.bee_todo_id, recorded by us
        # when we created it (recall/dispatch.py) -- never an external id.
        client.delete_todo(dispatch.bee_todo_id)

        feedback_text = _feedback_todo_text(answer, question)
        resp = client.create_todo(feedback_text, alarm_at=datetime.now(UTC).isoformat(timespec="seconds"))

    db = get_session()
    try:
        db.add(FeedbackTodo(bee_todo_id=resp["id"]))
        db.commit()
    finally:
        db.close()

    return answer


def cleanup_old_feedback_todos(now: datetime | None = None) -> int:
    """Deletes Recall feedback todos older than 12h. Hard guard: only ids
    present in the FeedbackTodo table (which this module itself populates
    when it creates a feedback todo) are ever candidates for deletion."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=FEEDBACK_TODO_MAX_AGE_HOURS)

    db = get_session()
    try:
        old = db.exec(select(FeedbackTodo).where(FeedbackTodo.created_at < cutoff)).all()
        ids = [f.bee_todo_id for f in old]
    finally:
        db.close()

    if not ids:
        return 0

    with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
        for todo_id in ids:
            client.delete_todo(todo_id)

    db = get_session()
    try:
        for f in db.exec(select(FeedbackTodo).where(FeedbackTodo.bee_todo_id.in_(ids))).all():
            db.delete(f)
        db.commit()
    finally:
        db.close()
    return len(ids)
