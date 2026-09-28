from datetime import UTC, datetime

from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from recall.db import Conversation, Dispatch, LLMCallLog, Question
from recall.db import Session as SessionModel
from recall.status import get_status

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def test_get_status_empty_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("recall.status.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.dispatch.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.llm.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.questions.get_session", lambda: DBSession(engine))

    status = get_status()

    assert status["profile"] in ("normal", "dense")
    assert status["dispatches_today"] == 0
    assert status["open_dispatch_id"] is None
    assert status["next_due_question_id"] is None
    assert status["llm_calls_used_today"] == 0
    assert status["pending_session_ids"] == []


def test_get_status_reflects_real_state(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("recall.status.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.dispatch.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.llm.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.questions.get_session", lambda: DBSession(engine))

    with DBSession(engine) as db:
        db.add(Conversation(id=1))
        s = SessionModel(start_at=BASE, end_at=BASE, conversation_ids=[1], approval_status="pending")
        db.add(s)
        q = Question(conversation_id=1, session_id=None, text="Q", expected_answer="A", source_excerpt="...")
        db.add(q)
        db.commit()
        db.refresh(q)
        db.add(Dispatch(question_id=q.id, bee_todo_id=1, alarm_at=BASE, status="pending", dispatched_at=BASE))
        from recall.dispatch import to_local

        db.add(LLMCallLog(date=to_local(datetime.now(UTC)).date().isoformat(), count=7))
        db.commit()
        db.refresh(s)
        session_id = s.id

    status = get_status()

    assert status["open_dispatch_id"] is not None
    assert status["llm_calls_used_today"] == 7
    assert status["pending_session_ids"] == [session_id]
