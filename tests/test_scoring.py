from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine, select

from recall.db import Answer, Conversation, Dispatch, Question, ReviewState, SessionRun
from recall.srs import sm2

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)  # noon local-ish, well clear of the 04:00 boundary


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("recall.scoring.get_session", lambda: DBSession(engine))
    return engine


# --- local_day_for / the 04:00 boundary ---


def test_local_day_for_before_reset_belongs_to_previous_day():
    from zoneinfo import ZoneInfo

    from recall.scoring import local_day_for

    tz = ZoneInfo("America/Los_Angeles")
    before_reset = datetime(2026, 1, 2, 3, 59, tzinfo=tz)  # 3:59am -- still "yesterday"
    assert local_day_for(before_reset.astimezone(UTC)) == "2026-01-01"


def test_local_day_for_after_reset_belongs_to_current_day():
    from zoneinfo import ZoneInfo

    from recall.scoring import local_day_for

    tz = ZoneInfo("America/Los_Angeles")
    after_reset = datetime(2026, 1, 2, 4, 1, tzinfo=tz)  # 4:01am -- now "today"
    assert local_day_for(after_reset.astimezone(UTC)) == "2026-01-02"


def test_local_day_for_exactly_at_reset_belongs_to_current_day():
    from zoneinfo import ZoneInfo

    from recall.scoring import local_day_for

    tz = ZoneInfo("America/Los_Angeles")
    at_reset = datetime(2026, 1, 2, 4, 0, tzinfo=tz)
    assert local_day_for(at_reset.astimezone(UTC)) == "2026-01-02"


# --- current_streak_days ---


def _completed_run(db, ended_at):
    db.add(SessionRun(trigger="tick", status="completed", started_at=ended_at - timedelta(minutes=5), ended_at=ended_at, questions_asked=1, questions_answered=1))


def test_streak_zero_with_no_completed_sessions(db_engine):
    from recall.scoring import current_streak_days

    assert current_streak_days(now=BASE) == 0


def test_streak_counts_consecutive_days_ending_today(db_engine):
    from recall.scoring import current_streak_days

    with DBSession(db_engine) as db:
        _completed_run(db, BASE)
        _completed_run(db, BASE - timedelta(days=1))
        _completed_run(db, BASE - timedelta(days=2))
        db.commit()

    assert current_streak_days(now=BASE) == 3


def test_streak_still_counts_if_today_not_met_yet(db_engine):
    """A streak isn't broken until a full day passes unmet -- if yesterday
    met the goal and today just hasn't happened yet, the streak holds."""
    from recall.scoring import current_streak_days

    with DBSession(db_engine) as db:
        _completed_run(db, BASE - timedelta(days=1))
        _completed_run(db, BASE - timedelta(days=2))
        db.commit()

    assert current_streak_days(now=BASE) == 2


def test_streak_broken_by_a_gap(db_engine):
    from recall.scoring import current_streak_days

    with DBSession(db_engine) as db:
        _completed_run(db, BASE)
        # gap: no session on BASE - 1 day
        _completed_run(db, BASE - timedelta(days=2))
        db.commit()

    assert current_streak_days(now=BASE) == 1


def test_ended_no_answer_session_does_not_count_toward_streak(db_engine):
    from recall.scoring import current_streak_days

    with DBSession(db_engine) as db:
        db.add(SessionRun(trigger="tick", status="ended_no_answer", started_at=BASE, ended_at=BASE))
        db.commit()

    assert current_streak_days(now=BASE) == 0


# --- recompute_past_no_answers ---


def _seed_question_with_history(engine, session_id=1):
    with DBSession(engine) as db:
        db.add(Conversation(id=1))
        q = Question(conversation_id=1, session_id=session_id, text="Q", expected_answer="A", rubric=["a", "b"], source_excerpt="...")
        db.add(q)
        db.commit()
        db.refresh(q)
        return q.id


def test_recompute_undoes_sm2_penalty_from_a_historical_no_answer(db_engine):
    """Simulates the pre-v4 bug: a correct review, then a no_answer that
    (incorrectly, under the old answer_flow.py rule) also fed quality=0
    through sm2(). Recompute should produce exactly what replaying ONLY
    the correct review yields -- the no_answer never touched."""
    from recall.scoring import recompute_past_no_answers

    question_id = _seed_question_with_history(db_engine)

    with DBSession(db_engine) as db:
        d1 = Dispatch(question_id=question_id, bee_todo_id=1, alarm_at=BASE, dispatched_at=BASE, status="answered")
        db.add(d1)
        db.commit()
        db.refresh(d1)
        matched1 = BASE + timedelta(seconds=30)
        db.add(Answer(dispatch_id=d1.id, raw_text="a real answer", utterance_ids=[], verdict="correct", quality=5, matched_at=matched1))

        d2 = Dispatch(question_id=question_id, bee_todo_id=2, alarm_at=BASE + timedelta(days=1), dispatched_at=BASE + timedelta(days=1), status="answered")
        db.add(d2)
        db.commit()
        db.refresh(d2)
        matched2 = BASE + timedelta(days=1, seconds=330)
        db.add(Answer(dispatch_id=d2.id, raw_text="", utterance_ids=[], verdict="no_answer", quality=0, matched_at=matched2))
        db.commit()

        # Simulate the OLD buggy path: both reviews fed through sm2() in order.
        r1 = sm2(5, 2.5, 0, 0)
        r2 = sm2(0, r1.ease_factor, r1.interval_days, r1.repetitions)  # the bug -- no_answer touching SM-2
        db.add(ReviewState(question_id=question_id, ease_factor=r2.ease_factor, interval_days=r2.interval_days, repetitions=r2.repetitions, due_at=matched2 + timedelta(days=r2.interval_days), last_reviewed_at=matched2))
        db.commit()

    changed = recompute_past_no_answers(now=BASE + timedelta(days=2))

    assert len(changed) == 1
    assert changed[0]["question_id"] == question_id

    expected = sm2(5, 2.5, 0, 0)  # only the correct review, no_answer skipped
    with DBSession(db_engine) as db:
        rs = db.exec(select(ReviewState).where(ReviewState.question_id == question_id)).first()
        assert rs.ease_factor == expected.ease_factor
        assert rs.interval_days == expected.interval_days
        assert rs.repetitions == expected.repetitions
        assert rs.due_at == matched1 + timedelta(days=expected.interval_days)


def test_recompute_reports_nothing_changed_when_already_correct(db_engine):
    """A question whose ReviewState already reflects only-real-reviews
    (e.g. graded under the new rule from the start) should report no
    change -- recompute must be idempotent/safe to rerun."""
    from recall.scoring import recompute_past_no_answers

    question_id = _seed_question_with_history(db_engine)

    with DBSession(db_engine) as db:
        d1 = Dispatch(question_id=question_id, bee_todo_id=1, alarm_at=BASE, dispatched_at=BASE, status="answered")
        db.add(d1)
        db.commit()
        db.refresh(d1)
        matched1 = BASE + timedelta(seconds=30)
        db.add(Answer(dispatch_id=d1.id, raw_text="a real answer", utterance_ids=[], verdict="correct", quality=5, matched_at=matched1))

        d2 = Dispatch(question_id=question_id, bee_todo_id=2, alarm_at=BASE + timedelta(days=1), dispatched_at=BASE + timedelta(days=1), status="answered")
        db.add(d2)
        db.commit()
        db.refresh(d2)
        matched2 = BASE + timedelta(days=1, seconds=330)
        db.add(Answer(dispatch_id=d2.id, raw_text="", utterance_ids=[], verdict="no_answer", quality=0, matched_at=matched2))
        db.commit()

        # Correct, already-fixed state: only the correct review applied.
        r1 = sm2(5, 2.5, 0, 0)
        db.add(ReviewState(question_id=question_id, ease_factor=r1.ease_factor, interval_days=r1.interval_days, repetitions=r1.repetitions, due_at=matched1 + timedelta(days=r1.interval_days), last_reviewed_at=matched1))
        db.commit()

    changed = recompute_past_no_answers(now=BASE + timedelta(days=2))
    assert changed == []


def test_recompute_skips_questions_with_no_no_answer_history(db_engine):
    from recall.scoring import recompute_past_no_answers

    question_id = _seed_question_with_history(db_engine)
    with DBSession(db_engine) as db:
        d1 = Dispatch(question_id=question_id, bee_todo_id=1, alarm_at=BASE, dispatched_at=BASE, status="answered")
        db.add(d1)
        db.commit()
        db.refresh(d1)
        db.add(Answer(dispatch_id=d1.id, raw_text="a real answer", utterance_ids=[], verdict="correct", quality=5, matched_at=BASE + timedelta(seconds=30)))
        db.commit()

    assert recompute_past_no_answers(now=BASE) == []
