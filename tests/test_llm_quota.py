import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from recall.llm import DAILY_CALL_CAP, DailyCallCapExceeded, _QuotaEnforcedProvider, calls_used_today
from recall.llm.base import GeneratedQuestion, GradeResult, LLMProvider, QuestionGenerationResult


class _FakeProvider(LLMProvider):
    def __init__(self):
        self.calls = 0

    def generate_questions(self, transcript, n, target_language=None):
        self.calls += 1
        return QuestionGenerationResult(session_type="casual", session_type_reason="r", questions=[])

    def grade_answer(self, question, spoken_answer):
        self.calls += 1
        return GradeResult(verdict="correct", rubric_points_hit=[], quality=5, feedback="ok")

    def shorten_question(self, text, max_chars):
        self.calls += 1
        return text[:max_chars]


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("recall.llm.get_session", lambda: DBSession(engine))
    return engine


def test_calls_used_today_starts_at_zero(db_engine):
    assert calls_used_today() == 0


def test_generate_questions_increments_counter(db_engine):
    provider = _QuotaEnforcedProvider(_FakeProvider())
    provider.generate_questions("t", n=1)
    assert calls_used_today() == 1
    provider.generate_questions("t", n=1)
    assert calls_used_today() == 2


def test_grade_answer_increments_counter(db_engine):
    provider = _QuotaEnforcedProvider(_FakeProvider())
    q = GeneratedQuestion(text="Q", expected_answer="A", rubric=[], source_excerpt="", topic="", language="")
    provider.grade_answer(q, "answer")
    assert calls_used_today() == 1


def test_refuses_call_over_daily_cap(db_engine):
    provider = _QuotaEnforcedProvider(_FakeProvider())
    for _ in range(DAILY_CALL_CAP):
        provider.generate_questions("t", n=1)
    assert calls_used_today() == DAILY_CALL_CAP

    with pytest.raises(DailyCallCapExceeded):
        provider.generate_questions("t", n=1)

    # refused call must not have reached the inner provider
    assert provider._inner.calls == DAILY_CALL_CAP


def test_cap_is_per_calendar_date(db_engine, monkeypatch):
    from recall.db import LLMCallLog

    with DBSession(db_engine) as db:
        db.add(LLMCallLog(date="2020-01-01", count=DAILY_CALL_CAP))
        db.commit()

    # today's date has its own fresh counter, unaffected by an old date's cap
    provider = _QuotaEnforcedProvider(_FakeProvider())
    provider.generate_questions("t", n=1)
    assert calls_used_today() == 1
