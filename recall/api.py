"""Phase 6 dashboard backend: read-mostly FastAPI app, run as its own
process (`uvicorn recall.api:app`), separate from the trial daemon
(`python -m recall.run`).

FROZEN this phase (live trial daemon running against this DB in WAL mode --
operator instruction, 2026-09-28): recall/run.py, dispatch.py,
answer_flow.py, matcher.py, grading.py, ingest.py. This module only reads
from them (importing constants/functions is fine, editing them is not).

Every GET route opens a dedicated SQLite *read-only* connection (`mode=ro`
URI), separate from recall.db's normal read-write engine, so this process
can never contend with or block the live daemon's writes -- WAL mode is
what makes concurrent readers safe here in the first place. The only
writes are the two approve/reject routes, which reuse recall.questions'
existing functions exactly (the same operations the CLI already performs),
each opening its own short-lived connection via recall.db.get_session().
"""

import difflib
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session as DBSession
from sqlmodel import create_engine, select

from recall.config import settings
from recall.db import DB_PATH, Answer, Dispatch, Question
from recall.db import Session as SessionModel
from recall.dispatch import dispatches_today, has_open_dispatch
from recall.matcher import DEFAULT_HARD_CAP_S
from recall.overrides import load_excluded_sessions
from recall.questions import reject_question, set_approval

READONLY_DB_URI = f"sqlite:///file:{Path(DB_PATH).resolve()}?mode=ro&uri=true"
_readonly_engine = create_engine(READONLY_DB_URI)

RUBRIC_FUZZY_THRESHOLD = 0.6
GRADED_VERDICTS_FOR_MASTERY_EXCLUDE = {"code_truncated"}  # Phase 4C: a tooling-bug tag, not a real grade


def _ro_session() -> DBSession:
    return DBSession(_readonly_engine)


def _to_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.astimezone(ZoneInfo(settings.timezone))


app = FastAPI(title="Recall Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# --- GET /api/today ---


def _window_status(alarm_at: datetime, now: datetime) -> str:
    if now < alarm_at:
        return "waiting"
    if now < alarm_at + timedelta(seconds=DEFAULT_HARD_CAP_S):
        return "listening"
    return "grading"


@app.get("/api/today")
def get_today() -> dict:
    now = datetime.now(UTC)
    db = _ro_session()
    try:
        todays = dispatches_today(db, now)
        open_dispatch = has_open_dispatch(db)

        open_dispatch_payload = None
        if open_dispatch is not None:
            question = db.get(Question, open_dispatch.question_id)
            open_dispatch_payload = {
                "dispatch_id": open_dispatch.id,
                "question_id": question.id if question else None,
                "question_text": question.text if question else None,
                "alarm_at": open_dispatch.alarm_at.isoformat(),
                "window_status": _window_status(open_dispatch.alarm_at, now),
            }

        recent_answers = db.exec(select(Answer).order_by(Answer.matched_at.desc()).limit(10)).all()
        answers_payload = []
        for a in recent_answers:
            dispatch = db.get(Dispatch, a.dispatch_id)
            question = db.get(Question, dispatch.question_id) if dispatch else None
            answers_payload.append(
                {
                    "answer_id": a.id,
                    "question_id": question.id if question else None,
                    "question_text": question.text if question else None,
                    "verdict": a.verdict,
                    "quality": a.quality,
                    "matched_at": a.matched_at.isoformat(),
                }
            )

        return {
            "profile": settings.recall_profile,
            "dispatches_today": len(todays),
            "open_dispatch": open_dispatch_payload,
            "recent_answers": answers_payload,
        }
    finally:
        db.close()


# --- GET /api/answers/{id} ---


def _rubric_hit_status(rubric: list[str], rubric_points_hit: list[str]) -> list[dict]:
    """Marks each rubric point hit/missed. Exact match first; otherwise a
    fuzzy match, since the grader is asked to quote-or-closely-paraphrase
    rubric points (recall/prompts/grade_answer.md), not reproduce them
    verbatim -- an exact-only comparison would under-count real hits."""
    result = []
    for point in rubric:
        hit = any(
            point.strip().lower() == h.strip().lower()
            or difflib.SequenceMatcher(None, point.lower(), h.lower()).ratio() >= RUBRIC_FUZZY_THRESHOLD
            for h in rubric_points_hit
        )
        result.append({"point": point, "hit": hit})
    return result


@app.get("/api/answers/{answer_id}")
def get_answer(answer_id: int) -> dict:
    db = _ro_session()
    try:
        answer = db.get(Answer, answer_id)
        if answer is None:
            raise HTTPException(status_code=404, detail="answer not found")
        dispatch = db.get(Dispatch, answer.dispatch_id)
        question = db.get(Question, dispatch.question_id) if dispatch else None
        session = db.get(SessionModel, question.session_id) if question and question.session_id else None

        # Exact text a wrist feedback todo would show -- reuses answer_flow's
        # own formatting function (frozen file, read-only import) so this
        # can never drift from what's actually sent.
        from recall.answer_flow import _feedback_todo_text

        feedback_text = _feedback_todo_text(answer, question) if question else None

        return {
            "answer_id": answer.id,
            "question": {"id": question.id, "text": question.text} if question else None,
            "rubric": _rubric_hit_status(question.rubric, answer.rubric_points_hit) if question else [],
            "answer_transcript": answer.raw_text,
            "verdict": answer.verdict,
            "quality": answer.quality,
            "feedback_text": feedback_text,
            "source_excerpt": question.source_excerpt if question else None,
            "session_datetime": _to_local(session.start_at).isoformat() if session and session.start_at else None,
        }
    finally:
        db.close()


# --- GET /api/mastery ---


@app.get("/api/mastery")
def get_mastery() -> dict:
    db = _ro_session()
    try:
        questions = db.exec(select(Question).where(Question.active == True)).all()  # noqa: E712
        by_topic: dict[str, list[Question]] = defaultdict(list)
        for q in questions:
            by_topic[q.topic or "(untopiced)"].append(q)

        topics = []
        # review-number x day grid, aggregated across ALL topics for the
        # single retention series the /progress page charts.
        review_day_qualities: dict[tuple[str, int], list[int]] = defaultdict(list)

        for topic, qs in by_topic.items():
            latest_qualities = []
            for q in qs:
                answers = db.exec(
                    select(Answer)
                    .join(Dispatch, Answer.dispatch_id == Dispatch.id)
                    .where(Dispatch.question_id == q.id)
                    .order_by(Answer.matched_at)
                ).all()
                answers = [a for a in answers if a.verdict not in GRADED_VERDICTS_FOR_MASTERY_EXCLUDE and a.quality is not None]
                if not answers:
                    continue
                latest_qualities.append(answers[-1].quality)
                for i, a in enumerate(answers, start=1):
                    day_key = _to_local(a.matched_at).date().isoformat()
                    review_day_qualities[(day_key, i)].append(a.quality)

            topics.append(
                {
                    "topic": topic,
                    "question_count": len(qs),
                    "average_latest_quality": (sum(latest_qualities) / len(latest_qualities)) if latest_qualities else None,
                }
            )

        days: dict[str, dict[int, float]] = defaultdict(dict)
        for (day_key, review_number), qualities in review_day_qualities.items():
            days[day_key][review_number] = sum(qualities) / len(qualities)

        retention_series = [
            {"day": day, "points": [{"review_number": rn, "average_quality": avg} for rn, avg in sorted(points.items())]}
            for day, points in sorted(days.items())
        ]

        return {"topics": sorted(topics, key=lambda t: t["topic"]), "retention_series": retention_series}
    finally:
        db.close()


# --- GET /api/sessions ---


@app.get("/api/sessions")
def get_sessions() -> dict:
    db = _ro_session()
    try:
        excluded = load_excluded_sessions()
        sessions = db.exec(select(SessionModel).order_by(SessionModel.start_at.desc())).all()

        payload = []
        for s in sessions:
            question_count = len(db.exec(select(Question).where(Question.session_id == s.id).where(Question.active == True)).all())  # noqa: E712
            duration_minutes = None
            if s.start_at and s.end_at:
                duration_minutes = round((s.end_at - s.start_at).total_seconds() / 60, 1)
            status = "excluded" if s.id in excluded else s.approval_status
            payload.append(
                {
                    "session_id": s.id,
                    "date": _to_local(s.start_at).isoformat() if s.start_at else None,
                    "duration_minutes": duration_minutes,
                    "session_type": s.session_type,
                    "session_type_reason": s.session_type_reason,
                    "status": status,
                    "question_count": question_count,
                }
            )
        return {"sessions": payload}
    finally:
        db.close()


# --- GET /api/privacy ---

PRIVACY_RULES = [
    "Recall never quizzes on anything other people said about themselves -- only the wearer's own learning sessions.",
    "Casual chit-chat and small talk are classified and skipped entirely -- no questions are ever generated from them.",
    "Personal conversations (about other people's relationships, circumstances, or feelings) are skipped entirely too.",
    "Recall never penalizes capture errors -- a garbled or cut-off recording is never held against the wearer.",
    "Spoken answers are never reused as source material for new questions (no self-feeding).",
    "Any session can be permanently excluded from question generation by the operator, regardless of its content.",
]


@app.get("/api/privacy")
def get_privacy() -> dict:
    db = _ro_session()
    try:
        skipped_sessions = db.exec(
            select(SessionModel).where(SessionModel.session_type.in_(["casual", "personal"]))
        ).all()
        skipped_by_type: dict[str, int] = defaultdict(int)
        for s in skipped_sessions:
            skipped_by_type[s.session_type] += 1

        excluded_count = len(load_excluded_sessions())
        rejected_questions = len(db.exec(select(Question).where(Question.active == False)).all())  # noqa: E712

        return {
            "sessions_skipped_by_type": dict(skipped_by_type),
            "excluded_sessions_count": excluded_count,
            "questions_rejected_count": rejected_questions,
            "rules": PRIVACY_RULES,
        }
    finally:
        db.close()


# --- writes: approve/reject (short-lived connections, same ops as the CLI) ---


@app.post("/api/sessions/{session_id}/approve")
def approve_session(session_id: int) -> dict:
    try:
        set_approval(session_id, "approved")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"session_id": session_id, "status": "approved"}


@app.post("/api/sessions/{session_id}/reject")
def reject_session(session_id: int) -> dict:
    try:
        set_approval(session_id, "rejected")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"session_id": session_id, "status": "rejected"}


@app.post("/api/questions/{question_id}/reject")
def reject_question_route(question_id: int) -> dict:
    try:
        reject_question(question_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"question_id": question_id, "active": False}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("recall.api:app", host="127.0.0.1", port=8000, reload=False)
