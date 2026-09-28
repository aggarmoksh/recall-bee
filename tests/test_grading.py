from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from recall.db import Answer, Conversation, Dispatch, Question
from recall.grading import MAX_FEEDBACK_WORDS, grade_and_persist, grade_match
from recall.llm.base import GeneratedQuestion, GradeResult, LLMProvider
from recall.matcher import MatchResult, Utterance

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class _FakeProvider(LLMProvider):
    def __init__(self, result: GradeResult):
        self.result = result
        self.called_with: tuple | None = None

    def generate_questions(self, transcript, n, target_language=None):
        raise NotImplementedError

    def grade_answer(self, question: GeneratedQuestion, spoken_answer: str) -> GradeResult:
        self.called_with = (question, spoken_answer)
        return self.result

    def shorten_question(self, text, max_chars):
        raise NotImplementedError


def _question(**overrides) -> Question:
    defaults = dict(
        id=1,
        conversation_id=1,
        text="Why did Rome kidnap the Sabine women?",
        expected_answer="To solve a gender imbalance.",
        rubric=["no women in Rome", "hosted a festival", "kidnapped during the party"],
        source_excerpt="...",
        topic="Roman history",
        language="English",
    )
    defaults.update(overrides)
    return Question(**defaults)


def _match(text: str, utterance_ids: list[int] | None = None) -> MatchResult:
    utterance_ids = utterance_ids if utterance_ids is not None else ([1] if text else [])
    utterances = [Utterance(id=uid, text=text, spoken_at=BASE) for uid in utterance_ids]
    return MatchResult(utterances=utterances, raw_text=text, trigger_found=False)


@pytest.fixture
def db_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def seeded_dispatch(db_engine):
    with DBSession(db_engine, expire_on_commit=False) as db:
        db.add(Conversation(id=1))
        q = _question()
        db.add(q)
        db.commit()
        db.refresh(q)
        dispatch = Dispatch(question_id=q.id, bee_todo_id=999, alarm_at=BASE)
        db.add(dispatch)
        db.commit()
        db.refresh(dispatch)
        return dispatch, q


def test_empty_window_is_no_answer_without_llm_call(monkeypatch):
    provider = _FakeProvider(GradeResult(verdict="correct", rubric_points_hit=[], quality=5, feedback="ok"))
    monkeypatch.setattr("recall.grading.get_provider", lambda: provider)

    result = grade_match(_question(), _match(""))

    assert result.verdict == "no_answer"
    assert result.quality == 0
    assert provider.called_with is None  # LLM never called


def test_non_empty_window_delegates_to_provider(monkeypatch):
    provider = _FakeProvider(
        GradeResult(verdict="correct", rubric_points_hit=["no women in Rome"], quality=5, feedback="Nailed it.")
    )
    monkeypatch.setattr("recall.grading.get_provider", lambda: provider)

    result = grade_match(_question(), _match("They kidnapped the Sabine women to fix the gender imbalance."))

    assert result.verdict == "correct"
    assert result.quality == 5
    assert provider.called_with is not None
    question_arg, answer_arg = provider.called_with
    assert answer_arg == "They kidnapped the Sabine women to fix the gender imbalance."
    assert question_arg.text == _question().text


_LONG_ANSWER = "this is a sufficiently long spoken answer with well over twelve words in it to avoid incomplete capture"


def test_feedback_truncated_to_max_words(monkeypatch):
    long_feedback = " ".join(f"word{i}" for i in range(30))
    provider = _FakeProvider(GradeResult(verdict="incorrect", rubric_points_hit=[], quality=1, feedback=long_feedback))
    monkeypatch.setattr("recall.grading.get_provider", lambda: provider)

    result = grade_match(_question(), _match(_LONG_ANSWER))

    assert len(result.feedback.rstrip(".").split()) <= MAX_FEEDBACK_WORDS + 1  # +1 tolerance for "..." token


def test_feedback_under_limit_is_untouched(monkeypatch):
    provider = _FakeProvider(GradeResult(verdict="correct", rubric_points_hit=[], quality=5, feedback="Short and sweet."))
    monkeypatch.setattr("recall.grading.get_provider", lambda: provider)

    result = grade_match(_question(), _match("some answer"))

    assert result.feedback == "Short and sweet."


def test_short_non_correct_verdict_becomes_incomplete_capture(monkeypatch):
    provider = _FakeProvider(GradeResult(verdict="partial", rubric_points_hit=["a"], quality=3, feedback="Missed b."))
    monkeypatch.setattr("recall.grading.get_provider", lambda: provider)

    result = grade_match(_question(), _match("They had a festival."))  # 4 words, non-empty but sparse

    assert result.verdict == "incomplete_capture"
    assert result.quality == 0
    assert result.feedback == "Only caught a few words, will ask again"


def test_short_correct_verdict_is_not_overridden(monkeypatch):
    """A short answer that the LLM still judged fully correct should stand --
    incomplete_capture only overrides non-correct verdicts."""
    provider = _FakeProvider(GradeResult(verdict="correct", rubric_points_hit=["a", "b"], quality=5, feedback="Nailed it."))
    monkeypatch.setattr("recall.grading.get_provider", lambda: provider)

    result = grade_match(_question(), _match("Yes exactly that."))  # 3 words

    assert result.verdict == "correct"


def test_long_answer_at_exactly_the_word_boundary_is_not_incomplete(monkeypatch):
    twelve_words = " ".join(["word"] * 12)
    provider = _FakeProvider(GradeResult(verdict="incorrect", rubric_points_hit=[], quality=1, feedback="Wrong."))
    monkeypatch.setattr("recall.grading.get_provider", lambda: provider)

    result = grade_match(_question(), _match(twelve_words))

    assert result.verdict == "incorrect"  # exactly 12 words meets the threshold, not "< 12"


def test_grade_and_persist_writes_answer_row(db_engine, seeded_dispatch, monkeypatch):
    dispatch, question = seeded_dispatch
    provider = _FakeProvider(
        GradeResult(verdict="partial", rubric_points_hit=["hosted a festival"], quality=3, feedback="Missed the kidnapping detail.")
    )
    monkeypatch.setattr("recall.grading.get_provider", lambda: provider)
    monkeypatch.setattr("recall.grading.get_session", lambda: DBSession(db_engine))

    long_text = "They had a big festival and invited all of the neighboring Sabine families to come celebrate with them."
    match = _match(long_text, utterance_ids=[10, 11])
    answer = grade_and_persist(dispatch, question, match)

    assert answer.id is not None
    assert answer.dispatch_id == dispatch.id
    assert answer.verdict == "partial"
    assert answer.rubric_points_hit == ["hosted a festival"]
    assert answer.quality == 3
    assert answer.utterance_ids == [10, 11]
    assert answer.raw_text == long_text

    with DBSession(db_engine) as db:
        loaded = db.get(Answer, answer.id)
        assert loaded.verdict == "partial"


def test_grade_and_persist_handles_no_answer_case(db_engine, seeded_dispatch, monkeypatch):
    dispatch, question = seeded_dispatch
    provider = _FakeProvider(GradeResult(verdict="correct", rubric_points_hit=[], quality=5, feedback="unused"))
    monkeypatch.setattr("recall.grading.get_provider", lambda: provider)
    monkeypatch.setattr("recall.grading.get_session", lambda: DBSession(db_engine))

    answer = grade_and_persist(dispatch, question, _match(""))

    assert answer.verdict == "no_answer"
    assert answer.quality == 0
    assert provider.called_with is None
