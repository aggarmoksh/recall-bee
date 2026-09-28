from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine, select

from recall.db import Conversation, Dispatch, Question, ReviewState
from recall.db import Session as SessionModel
from recall.dispatch import (
    MAX_DISPATCHES_PER_DAY,
    MIN_GAP_MINUTES,
    PROFILES,
    _in_quiet_hours,
    _todo_text,
    active_profile,
    compute_next_alarm,
    dispatch_next,
    has_open_dispatch,
    next_due_question,
)

TZ = ZoneInfo("America/Los_Angeles")


def _local(hour, minute=0, day=15) -> datetime:
    return datetime(2026, 1, day, hour, minute, tzinfo=TZ).astimezone(UTC)


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("recall.dispatch.get_session", lambda: DBSession(engine))
    return engine


def _make_question(db, session_id=1, active=True) -> Question:
    q = Question(
        conversation_id=1, session_id=session_id, text="Why?", expected_answer="Because.",
        rubric=["a"], source_excerpt="...", active=active,
    )
    db.add(q)
    db.commit()
    db.refresh(q)
    return q


def _seed_session(engine, session_id_hint=None) -> int:
    with DBSession(engine) as db:
        db.add(Conversation(id=1))
        session = SessionModel(start_at=_local(9), end_at=_local(10), conversation_ids=[1], approval_status="approved")
        db.add(session)
        db.commit()
        db.refresh(session)
        return session.id


def _approved_session(db, session_id: int = 1) -> None:
    """For tests that use _make_question's default session_id=1 without
    going through _seed_session -- next_due_question now requires the
    question's session to be approved."""
    db.add(SessionModel(id=session_id, start_at=_local(9), end_at=_local(10), conversation_ids=[1], approval_status="approved"))
    db.commit()


# --- dispatch profiles ---

def test_active_profile_normal_matches_module_constants():
    profile = active_profile("normal")
    assert profile["max_per_day"] == MAX_DISPATCHES_PER_DAY
    assert profile["min_gap_minutes"] == MIN_GAP_MINUTES


def test_active_profile_dense_values():
    profile = active_profile("dense")
    assert profile["max_per_day"] == 25
    assert profile["min_gap_minutes"] == 15
    assert profile["first_review_hours"] == 0.5


def test_active_profile_defaults_to_settings(monkeypatch):
    # settings is a frozen dataclass -- swap the whole module-level instance
    # dispatch.py reads, rather than trying to mutate a frozen field.
    import recall.dispatch as dispatch_module
    from recall.config import Settings

    fake_settings = Settings(
        bee_proxy_port=8787,
        llm_provider="bedrock",
        llm_model="m",
        gemini_api_key="",
        anthropic_api_key="",
        aws_region="us-east-1",
        timezone="America/Los_Angeles",
        recall_profile="dense",
    )
    monkeypatch.setattr(dispatch_module, "settings", fake_settings)
    assert active_profile() == PROFILES["dense"]


def test_active_profile_rejects_invalid_name():
    with pytest.raises(ValueError):
        active_profile("bogus")


def test_dispatch_next_dense_profile_allows_15min_gap(db_engine):
    session_id = _seed_session(db_engine)
    with DBSession(db_engine) as db:
        from recall.db import Answer

        q1 = _make_question(db, session_id=session_id)
        d = Dispatch(question_id=q1.id, bee_todo_id=1, alarm_at=_local(11), status="pending", dispatched_at=_local(11, 40))
        db.add(d)
        db.commit()
        db.refresh(d)
        db.add(Answer(dispatch_id=d.id, raw_text="a", utterance_ids=[]))
        db.commit()
        _make_question(db, session_id=session_id)

    just_past_15min_gap = _local(11, 40) + timedelta(minutes=16)
    # 16 min gap: fails under "normal" (needs 45 min), passes under "dense" (needs 15 min)
    assert dispatch_next(dry_run=True, now=just_past_15min_gap, profile="normal") is None
    assert dispatch_next(dry_run=True, now=just_past_15min_gap, profile="dense") is not None


# --- pure function tests ---

def test_quiet_hours_late_night():
    assert _in_quiet_hours(_local(23))
    assert _in_quiet_hours(_local(2))
    assert _in_quiet_hours(_local(7, 59))


def test_quiet_hours_daytime_not_quiet():
    assert not _in_quiet_hours(_local(8, 0))
    assert not _in_quiet_hours(_local(12))
    assert not _in_quiet_hours(_local(21, 59))


def test_todo_text_short_question_untouched():
    q = Question(conversation_id=1, text="Why?", expected_answer="A", source_excerpt="...")
    assert _todo_text(q) == "Recall: Why?"


def test_todo_text_truncates_long_question():
    long_text = "Explain " + "very " * 40 + "long question?"
    q = Question(conversation_id=1, text=long_text, expected_answer="A", source_excerpt="...")
    result = _todo_text(q)
    from recall.dispatch import TODO_TEXT_MAX_CHARS

    assert len(result) <= TODO_TEXT_MAX_CHARS
    assert result.endswith("…")


# --- has_open_dispatch ---

def test_has_open_dispatch_none_when_empty(db_engine):
    with DBSession(db_engine) as db:
        assert has_open_dispatch(db) is None


def test_has_open_dispatch_finds_unanswered(db_engine):
    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        q = _make_question(db)
        d = Dispatch(question_id=q.id, bee_todo_id=1, alarm_at=_local(10), status="pending")
        db.add(d)
        db.commit()

        assert has_open_dispatch(db) is not None


def test_has_open_dispatch_ignores_answered(db_engine):
    from recall.db import Answer

    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        q = _make_question(db)
        d = Dispatch(question_id=q.id, bee_todo_id=1, alarm_at=_local(10), status="pending")
        db.add(d)
        db.commit()
        db.refresh(d)
        db.add(Answer(dispatch_id=d.id, raw_text="answer", utterance_ids=[]))
        db.commit()

        assert has_open_dispatch(db) is None


# --- next_due_question ---

def test_next_due_question_prefers_never_dispatched(db_engine):
    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        _approved_session(db)
        q1 = _make_question(db)
        q2 = _make_question(db)
        result = next_due_question(db, _local(12))
        assert result.id == q1.id  # oldest never-dispatched, by id order


def test_next_due_question_skips_inactive(db_engine):
    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        _approved_session(db)
        _make_question(db, active=False)
        q2 = _make_question(db, active=True)
        result = next_due_question(db, _local(12))
        assert result.id == q2.id


def test_next_due_question_falls_back_to_review_state(db_engine):
    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        _approved_session(db)
        q = _make_question(db)
        # mark as already dispatched
        db.add(Dispatch(question_id=q.id, bee_todo_id=1, alarm_at=_local(9), status="pending"))
        db.add(ReviewState(question_id=q.id, due_at=_local(11)))
        db.commit()

        result = next_due_question(db, _local(12))
        assert result.id == q.id


def test_next_due_question_none_when_review_not_yet_due(db_engine):
    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        _approved_session(db)
        q = _make_question(db)
        db.add(Dispatch(question_id=q.id, bee_todo_id=1, alarm_at=_local(9), status="pending"))
        db.add(ReviewState(question_id=q.id, due_at=_local(15)))  # due later than "now"
        db.commit()

        result = next_due_question(db, _local(12))
        assert result is None


# --- compute_next_alarm ---

def test_compute_next_alarm_first_review_anchors_on_session_end(db_engine):
    session_id = _seed_session(db_engine)
    with DBSession(db_engine) as db:
        q = _make_question(db, session_id=session_id)
        alarm = compute_next_alarm(db, q, _local(10, 30))
        session = db.get(SessionModel, session_id)
        assert alarm == session.end_at + timedelta(hours=2)


def test_compute_next_alarm_uses_review_state_when_present(db_engine):
    session_id = _seed_session(db_engine)
    with DBSession(db_engine) as db:
        q = _make_question(db, session_id=session_id)
        db.add(ReviewState(question_id=q.id, due_at=_local(20)))
        db.commit()
        alarm = compute_next_alarm(db, q, _local(12))
        assert alarm == _local(20)


# --- dispatch_next (dry_run only -- avoids real Bee calls) ---

def test_dispatch_next_respects_quiet_hours(db_engine):
    session_id = _seed_session(db_engine)
    with DBSession(db_engine) as db:
        _make_question(db, session_id=session_id)
    assert dispatch_next(dry_run=True, now=_local(23)) is None


def test_dispatch_next_creates_dispatch_and_review_state(db_engine):
    session_id = _seed_session(db_engine)
    with DBSession(db_engine) as db:
        q = _make_question(db, session_id=session_id)
        q_id = q.id

    result = dispatch_next(dry_run=True, now=_local(12))

    assert result is not None
    assert result.question_id == q_id
    assert result.bee_todo_id < 0  # dry-run placeholder

    with DBSession(db_engine) as db:
        assert len(db.exec(select(Dispatch)).all()) == 1
        assert len(db.exec(select(ReviewState)).all()) == 1


def test_dispatch_next_respects_open_dispatch(db_engine):
    session_id = _seed_session(db_engine)
    with DBSession(db_engine) as db:
        q1 = _make_question(db, session_id=session_id)
        q2 = _make_question(db, session_id=session_id)
        db.add(Dispatch(question_id=q1.id, bee_todo_id=1, alarm_at=_local(9), status="pending"))
        db.commit()

    assert dispatch_next(dry_run=True, now=_local(12)) is None


def test_dispatch_next_respects_daily_cap(db_engine):
    session_id = _seed_session(db_engine)
    with DBSession(db_engine) as db:
        from recall.db import Answer

        for i in range(MAX_DISPATCHES_PER_DAY):
            q = _make_question(db, session_id=session_id)
            d = Dispatch(question_id=q.id, bee_todo_id=100 + i, alarm_at=_local(9), status="pending", dispatched_at=_local(9, i))
            db.add(d)
            db.commit()
            db.refresh(d)
            db.add(Answer(dispatch_id=d.id, raw_text="a", utterance_ids=[]))  # close it out
            db.commit()
        _make_question(db, session_id=session_id)  # one more, fresh question available

    assert dispatch_next(dry_run=True, now=_local(15)) is None


def test_dispatch_next_respects_min_gap(db_engine):
    session_id = _seed_session(db_engine)
    with DBSession(db_engine) as db:
        from recall.db import Answer

        q1 = _make_question(db, session_id=session_id)
        d = Dispatch(question_id=q1.id, bee_todo_id=1, alarm_at=_local(11), status="pending", dispatched_at=_local(11, 50))
        db.add(d)
        db.commit()
        db.refresh(d)
        db.add(Answer(dispatch_id=d.id, raw_text="a", utterance_ids=[]))
        db.commit()
        _make_question(db, session_id=session_id)

    just_inside_gap = _local(11, 50) + timedelta(minutes=MIN_GAP_MINUTES - 1)
    assert dispatch_next(dry_run=True, now=just_inside_gap) is None


def test_dispatch_next_none_when_no_questions(db_engine):
    assert dispatch_next(dry_run=True, now=_local(12)) is None


def test_force_bypasses_quiet_hours_cap_and_gap(db_engine):
    session_id = _seed_session(db_engine)
    with DBSession(db_engine) as db:
        from recall.db import Answer

        for i in range(MAX_DISPATCHES_PER_DAY):
            q = _make_question(db, session_id=session_id)
            d = Dispatch(question_id=q.id, bee_todo_id=100 + i, alarm_at=_local(9), status="pending", dispatched_at=_local(23, i))
            db.add(d)
            db.commit()
            db.refresh(d)
            db.add(Answer(dispatch_id=d.id, raw_text="a", utterance_ids=[]))
            db.commit()
        _make_question(db, session_id=session_id)

    # at the cap, inside quiet hours, inside the min-gap -- force overrides all three
    result = dispatch_next(dry_run=True, now=_local(23, 30), force=True)
    assert result is not None


def test_force_still_respects_open_dispatch(db_engine):
    session_id = _seed_session(db_engine)
    with DBSession(db_engine) as db:
        q1 = _make_question(db, session_id=session_id)
        _make_question(db, session_id=session_id)
        db.add(Dispatch(question_id=q1.id, bee_todo_id=1, alarm_at=_local(9), status="pending"))
        db.commit()

    assert dispatch_next(dry_run=True, now=_local(12), force=True) is None
