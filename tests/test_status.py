from datetime import UTC, datetime

from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from recall.db import Conversation, LLMCallLog, Question
from recall.db import Session as SessionModel
from recall.dispatch import to_local
from recall.status import get_status

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _patch_all(monkeypatch, engine):
    monkeypatch.setattr("recall.status.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.session.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.scoring.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.llm.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.questions.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.heartbeat.get_session", lambda: DBSession(engine))


def test_get_status_empty_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    _patch_all(monkeypatch, engine)

    status = get_status()

    assert status["daily_quota"] == 6
    assert status["answered_today"] == 0
    assert status["goal_met"] is False
    assert status["streak_days"] == 0
    assert status["session_state"] == "idle"
    assert status["current_question_id"] is None
    assert status["due_count"] == 0
    assert status["llm_calls_used_today"] == 0
    assert status["pending_session_ids"] == []


def test_get_status_reflects_pending_session_and_llm_calls(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    _patch_all(monkeypatch, engine)

    with DBSession(engine) as db:
        db.add(Conversation(id=1))
        s = SessionModel(start_at=BASE, end_at=BASE, conversation_ids=[1], approval_status="pending")
        db.add(s)
        q = Question(conversation_id=1, session_id=None, text="Q", expected_answer="A", source_excerpt="...")
        db.add(q)
        db.commit()
        db.refresh(s)
        session_id = s.id

        db.add(LLMCallLog(date=to_local(datetime.now(UTC)).date().isoformat(), count=7))
        db.commit()

    status = get_status()

    assert status["llm_calls_used_today"] == 7
    assert status["pending_session_ids"] == [session_id]
    assert status["due_count"] == 0  # the question's session is pending, not approved -- not due


def test_get_status_reflects_active_session_state(tmp_path, monkeypatch):
    from recall.db import Dispatch, SessionRun

    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    _patch_all(monkeypatch, engine)

    with DBSession(engine) as db:
        db.add(Conversation(id=1))
        s = SessionModel(start_at=BASE, end_at=BASE, conversation_ids=[1], approval_status="approved")
        db.add(s)
        db.commit()
        db.refresh(s)
        q = Question(conversation_id=1, session_id=s.id, text="Q", expected_answer="A", rubric=["a"], source_excerpt="...")
        db.add(q)
        db.commit()
        db.refresh(q)
        run = SessionRun(trigger="tick", status="active", started_at=BASE, questions_asked=2)
        db.add(run)
        db.commit()
        db.refresh(run)
        db.add(Dispatch(question_id=q.id, bee_todo_id=1, alarm_at=BASE, status="pending", dispatched_at=BASE, session_run_id=run.id))
        db.commit()
        question_id = q.id

    status = get_status()

    assert "in session" in status["session_state"]
    assert status["current_question_id"] == question_id
