from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine, select

from recall.db import Answer, Conversation, Dispatch, Question
from recall.db import Session as SessionModel
from recall.matcher import DEFAULT_HARD_CAP_S
from recall.run import (
    ANSWER_WINDOW_SAFETY_BUFFER_S,
    SESSION_CLOSED_GAP_MINUTES,
    find_closed_unclassified_sessions,
    find_dispatches_ready_for_answer_flow,
)

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def db_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    return engine


def test_closed_session_past_gap_is_found(db_engine, monkeypatch):
    monkeypatch.setattr("recall.run.load_excluded_sessions", lambda: set())
    with DBSession(db_engine) as db:
        s = SessionModel(start_at=BASE, end_at=BASE + timedelta(minutes=5), conversation_ids=[1])
        db.add(s)
        db.commit()
        db.refresh(s)
        now = s.end_at + timedelta(minutes=SESSION_CLOSED_GAP_MINUTES + 1)
        result = find_closed_unclassified_sessions(db, now)
        assert len(result) == 1
        assert result[0].id == s.id


def test_session_not_yet_closed_is_excluded(db_engine, monkeypatch):
    monkeypatch.setattr("recall.run.load_excluded_sessions", lambda: set())
    with DBSession(db_engine) as db:
        s = SessionModel(start_at=BASE, end_at=BASE + timedelta(minutes=5), conversation_ids=[1])
        db.add(s)
        db.commit()
        now = s.end_at + timedelta(minutes=SESSION_CLOSED_GAP_MINUTES - 1)
        assert find_closed_unclassified_sessions(db, now) == []


def test_already_classified_session_is_excluded(db_engine, monkeypatch):
    monkeypatch.setattr("recall.run.load_excluded_sessions", lambda: set())
    with DBSession(db_engine) as db:
        s = SessionModel(start_at=BASE, end_at=BASE + timedelta(minutes=5), conversation_ids=[1], session_type="lecture_study")
        db.add(s)
        db.commit()
        now = s.end_at + timedelta(hours=1)
        assert find_closed_unclassified_sessions(db, now) == []


def test_manually_excluded_session_is_skipped(db_engine, monkeypatch):
    with DBSession(db_engine) as db:
        s = SessionModel(start_at=BASE, end_at=BASE + timedelta(minutes=5), conversation_ids=[1])
        db.add(s)
        db.commit()
        db.refresh(s)
        monkeypatch.setattr("recall.run.load_excluded_sessions", lambda: {s.id})
        now = s.end_at + timedelta(hours=1)
        assert find_closed_unclassified_sessions(db, now) == []


def test_dispatch_ready_once_hard_cap_plus_buffer_elapsed(db_engine):
    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        q = Question(conversation_id=1, text="Q", expected_answer="A", source_excerpt="...")
        db.add(q)
        db.commit()
        db.refresh(q)
        d = Dispatch(question_id=q.id, bee_todo_id=1, alarm_at=BASE, status="pending")
        db.add(d)
        db.commit()

        too_early = BASE + timedelta(seconds=DEFAULT_HARD_CAP_S + ANSWER_WINDOW_SAFETY_BUFFER_S - 1)
        assert find_dispatches_ready_for_answer_flow(db, too_early) == []

        ready_time = BASE + timedelta(seconds=DEFAULT_HARD_CAP_S + ANSWER_WINDOW_SAFETY_BUFFER_S)
        result = find_dispatches_ready_for_answer_flow(db, ready_time)
        assert len(result) == 1
        assert result[0].id == d.id


def test_already_answered_dispatch_is_not_ready_again(db_engine):
    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        q = Question(conversation_id=1, text="Q", expected_answer="A", source_excerpt="...")
        db.add(q)
        db.commit()
        db.refresh(q)
        d = Dispatch(question_id=q.id, bee_todo_id=1, alarm_at=BASE, status="answered")
        db.add(d)
        db.commit()
        db.refresh(d)
        db.add(Answer(dispatch_id=d.id, raw_text="already graded", utterance_ids=[]))
        db.commit()

        later = BASE + timedelta(hours=1)
        assert find_dispatches_ready_for_answer_flow(db, later) == []
