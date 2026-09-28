import itertools
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from recall.db import Answer, Conversation, Dispatch, Question
from recall.review import _first_n_words, answers_last_24h, append_daily_summary, daily_summary

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_todo_id_counter = itertools.count(1)


def test_first_n_words_short_text_untouched():
    assert _first_n_words("one two three") == "one two three"


def test_first_n_words_truncates_long_text():
    text = " ".join(f"word{i}" for i in range(30))
    result = _first_n_words(text, n=12)
    assert result == "word0 word1 word2 word3 word4 word5 word6 word7 word8 word9 word10 word11..."


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("recall.review.get_session", lambda: DBSession(engine))
    return engine


def _seed_answer(engine, matched_at, verdict, quality, operator_agrees=None):
    with DBSession(engine) as db:
        if db.get(Conversation, 1) is None:
            db.add(Conversation(id=1))
            db.commit()
        q = Question(conversation_id=1, text="Q", expected_answer="A", source_excerpt="...")
        db.add(q)
        db.commit()
        db.refresh(q)
        d = Dispatch(question_id=q.id, bee_todo_id=next(_todo_id_counter), alarm_at=BASE, dispatched_at=matched_at)
        db.add(d)
        db.commit()
        db.refresh(d)
        a = Answer(
            dispatch_id=d.id,
            raw_text="some answer text here",
            verdict=verdict,
            quality=quality,
            matched_at=matched_at,
            operator_agrees=operator_agrees,
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        return a.id


def test_answers_last_24h_excludes_older(db_engine):
    _seed_answer(db_engine, BASE - timedelta(hours=25), "correct", 5)
    _seed_answer(db_engine, BASE - timedelta(hours=1), "correct", 5)

    with DBSession(db_engine) as db:
        result = answers_last_24h(db, now=BASE)

    assert len(result) == 1


def test_daily_summary_counts_verdicts(db_engine):
    _seed_answer(db_engine, BASE - timedelta(hours=1), "correct", 5)
    _seed_answer(db_engine, BASE - timedelta(hours=2), "no_answer", 0)
    _seed_answer(db_engine, BASE - timedelta(hours=3), "incomplete_capture", 0)
    _seed_answer(db_engine, BASE - timedelta(hours=4), "partial", 3, operator_agrees=True)
    _seed_answer(db_engine, BASE - timedelta(hours=5), "incorrect", 1, operator_agrees=False)

    summary = daily_summary(now=BASE)

    assert summary["answered"] == 5
    assert summary["no_answer"] == 1
    assert summary["incomplete_capture"] == 1
    assert summary["agreement_pct"] == 50.0  # 1 of 2 reviewed agreed
    assert summary["mean_quality"] == pytest.approx((5 + 0 + 0 + 3 + 1) / 5)


def test_daily_summary_excludes_code_truncated_from_agreement(db_engine):
    """code_truncated (Phase 4C) is a manual correction tag for an answer
    whose real content was cut by a matcher bug, not a real grading
    outcome -- must never count toward grader-agreement stats even if an
    operator later (mistakenly) marks it reviewed."""
    _seed_answer(db_engine, BASE - timedelta(hours=1), "correct", 5, operator_agrees=True)
    _seed_answer(db_engine, BASE - timedelta(hours=2), "code_truncated", 0, operator_agrees=False)

    summary = daily_summary(now=BASE)

    assert summary["answered"] == 2
    assert summary["agreement_pct"] == 100.0  # only the "correct" one counts


def test_daily_summary_handles_no_data(db_engine):
    summary = daily_summary(now=BASE)
    assert summary["answered"] == 0
    assert summary["agreement_pct"] is None
    assert summary["mean_quality"] is None


def test_append_daily_summary_writes_no_transcript_text(db_engine, monkeypatch, tmp_path):
    log_path = tmp_path / "dogfood_log.md"
    monkeypatch.setattr("recall.review.DOGFOOD_LOG_PATH", log_path)

    _seed_answer(db_engine, BASE - timedelta(hours=1), "correct", 5)
    append_daily_summary(now=BASE)

    content = log_path.read_text(encoding="utf-8")
    assert "some answer text here" not in content  # no raw_text
    assert "Prompts sent:" in content
    assert "Answered: 1" in content
