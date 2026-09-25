from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine, select

from recall.db import Answer, Conversation, Dispatch, Question, Session, Utterance


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")  # in-memory, isolated per test
    SQLModel.metadata.create_all(eng)
    return eng


def test_session_round_trips_conversation_ids_json_list(engine):
    with DBSession(engine) as db:
        s = Session(start_at=datetime.now(UTC), conversation_ids=[101, 102, 103])
        db.add(s)
        db.commit()
        db.refresh(s)
        session_id = s.id

    with DBSession(engine) as db:
        loaded = db.get(Session, session_id)
        assert loaded.conversation_ids == [101, 102, 103]


def test_question_links_to_session_with_source_window(engine):
    with DBSession(engine) as db:
        conv = Conversation(id=1)
        sess = Session(start_at=datetime.now(UTC))
        db.add(conv)
        db.add(sess)
        db.commit()
        db.refresh(sess)

        q = Question(
            conversation_id=conv.id,
            session_id=sess.id,
            source_start=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
            source_end=datetime(2026, 1, 1, 12, 5, 0, tzinfo=UTC),
            text="What is the capital of France?",
            expected_answer="Paris",
            source_excerpt="...the capital of France is Paris...",
        )
        db.add(q)
        db.commit()
        db.refresh(q)
        assert q.session_id == sess.id
        assert q.source_end > q.source_start


def test_answer_dispatch_id_is_unique(engine):
    with DBSession(engine) as db:
        conv = Conversation(id=1)
        db.add(conv)
        db.commit()

        q = Question(conversation_id=1, text="Q", expected_answer="A", source_excerpt="...")
        db.add(q)
        db.commit()
        db.refresh(q)

        dispatch = Dispatch(question_id=q.id, bee_todo_id=555, alarm_at=datetime.now(UTC))
        db.add(dispatch)
        db.commit()
        db.refresh(dispatch)

        db.add(Answer(dispatch_id=dispatch.id, raw_text="first answer", utterance_ids=[1, 2]))
        db.commit()

        db.add(Answer(dispatch_id=dispatch.id, raw_text="duplicate answer", utterance_ids=[3]))
        with pytest.raises(IntegrityError):
            db.commit()


def test_answer_utterance_ids_round_trips_as_json_list(engine):
    with DBSession(engine) as db:
        conv = Conversation(id=1)
        db.add(conv)
        q = Question(conversation_id=1, text="Q", expected_answer="A", source_excerpt="...")
        db.add(q)
        db.commit()
        db.refresh(q)

        dispatch = Dispatch(question_id=q.id, bee_todo_id=777, alarm_at=datetime.now(UTC))
        db.add(dispatch)
        db.commit()
        db.refresh(dispatch)

        answer = Answer(dispatch_id=dispatch.id, raw_text="combined text", utterance_ids=[10, 11, 12])
        db.add(answer)
        db.commit()
        answer_id = answer.id

    with DBSession(engine) as db:
        loaded = db.get(Answer, answer_id)
        assert loaded.utterance_ids == [10, 11, 12]


def test_utterance_stores_spoken_at_and_conversation_uuid(engine):
    with DBSession(engine) as db:
        conv = Conversation(id=1, conversation_uuid="abc-123")
        db.add(conv)
        db.commit()

        u = Utterance(
            id=999,
            conversation_id=1,
            conversation_uuid="abc-123",
            text="hello",
            spoken_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        )
        db.add(u)
        db.commit()

    with DBSession(engine) as db:
        loaded = db.exec(select(Utterance).where(Utterance.id == 999)).one()
        assert loaded.conversation_uuid == "abc-123"
        assert loaded.spoken_at == datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
