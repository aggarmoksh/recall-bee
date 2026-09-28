from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine, select

from recall.db import Answer, Conversation, Dispatch, Question
from recall.db import Session as SessionModel
from recall.db import Utterance
from recall.llm.base import GeneratedQuestion, LLMProvider, QuestionGenerationResult
from recall.questions import (
    generate_for_session,
    list_pending,
    locate_source_window,
    reject_question,
    retire_sessions,
    set_approval,
    set_type,
)

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class _FakeProvider(LLMProvider):
    """Returns a fixed classification + questions, ignoring the transcript."""

    def __init__(self, session_type: str, n_questions: int = 2, question_texts: list[str] | None = None):
        self.session_type = session_type
        self.n_questions = n_questions
        self.question_texts = question_texts
        self.shorten_calls: list[tuple[str, int]] = []

    def generate_questions(self, transcript, n, target_language=None):
        texts = self.question_texts if self.question_texts is not None else [f"Question {i}?" for i in range(self.n_questions)]
        questions = [
            GeneratedQuestion(
                text=text,
                expected_answer=f"Answer {i}",
                rubric=["point a", "point b"],
                source_excerpt="some excerpt",
                topic="Topic",
                language="English",
            )
            for i, text in enumerate(texts)
        ]
        return QuestionGenerationResult(
            session_type=self.session_type,
            session_type_reason="fake reason for testing",
            questions=questions,
        )

    def grade_answer(self, question, spoken_answer):
        raise NotImplementedError

    def shorten_question(self, text, max_chars):
        self.shorten_calls.append((text, max_chars))
        return text[:max_chars]


@pytest.fixture
def db_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def seeded_session(db_engine):
    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        db.add_all(
            [
                Utterance(id=1, conversation_id=1, conversation_uuid="x", text="hello world", spoken_at=BASE),
                Utterance(id=2, conversation_id=1, conversation_uuid="x", text="more content", spoken_at=BASE + timedelta(minutes=1)),
            ]
        )
        session = SessionModel(start_at=BASE, end_at=BASE + timedelta(minutes=1), conversation_ids=[1])
        db.add(session)
        db.commit()
        db.refresh(session)
        return session.id


def _patch(monkeypatch, engine, provider):
    monkeypatch.setattr("recall.questions.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.questions.get_provider", lambda: provider)


def test_enforce_question_length_leaves_short_text_untouched():
    from recall.questions import enforce_question_length

    provider = _FakeProvider("lecture_study")
    short_text = "Why?"
    assert enforce_question_length(short_text, provider) == short_text
    assert provider.shorten_calls == []


def test_enforce_question_length_shortens_over_length_text():
    from recall.questions import MAX_QUESTION_CHARS, enforce_question_length

    provider = _FakeProvider("lecture_study")
    long_text = "Explain " + "a very long rambling question " * 5 + "in detail?"
    assert len(long_text) > MAX_QUESTION_CHARS
    result = enforce_question_length(long_text, provider)
    assert result is not None
    assert len(result) <= MAX_QUESTION_CHARS
    assert provider.shorten_calls == [(long_text, MAX_QUESTION_CHARS)]


def test_enforce_question_length_drops_if_still_too_long_after_one_attempt():
    from recall.questions import enforce_question_length

    class _StubbornProvider(_FakeProvider):
        def shorten_question(self, text, max_chars):
            self.shorten_calls.append((text, max_chars))
            return text  # "shortening" that doesn't actually shorten

    provider = _StubbornProvider("lecture_study")
    long_text = "x" * 150
    result = enforce_question_length(long_text, provider)
    assert result is None
    assert len(provider.shorten_calls) == 1  # only one attempt, never retried


def test_generate_for_session_drops_question_still_too_long_after_shortening(db_engine, seeded_session, monkeypatch):
    class _StubbornProvider(_FakeProvider):
        def shorten_question(self, text, max_chars):
            self.shorten_calls.append((text, max_chars))
            return text

    long_text = "y" * 150
    provider = _StubbornProvider("lecture_study", question_texts=["Short one?", long_text])
    _patch(monkeypatch, db_engine, provider)

    questions = generate_for_session(seeded_session, n=2)

    assert len(questions) == 1
    assert questions[0].text == "Short one?"


def test_generate_for_session_persists_shortened_text(db_engine, seeded_session, monkeypatch):
    from recall.questions import MAX_QUESTION_CHARS

    long_text = "Explain " + "a very long rambling question " * 5 + "in detail?"
    provider = _FakeProvider("lecture_study", question_texts=[long_text])
    _patch(monkeypatch, db_engine, provider)

    questions = generate_for_session(seeded_session, n=1)

    assert len(questions) == 1
    assert len(questions[0].text) <= MAX_QUESTION_CHARS
    assert questions[0].text != long_text


def test_shorten_active_questions_reports_counts(db_engine, seeded_session, monkeypatch):
    from recall.questions import shorten_active_questions

    provider = _FakeProvider("lecture_study")
    _patch(monkeypatch, db_engine, provider)

    with DBSession(db_engine) as db:
        db.add(Question(conversation_id=1, session_id=seeded_session, text="short", expected_answer="a", source_excerpt="...", active=True))
        db.add(
            Question(
                conversation_id=1, session_id=seeded_session, text="z" * 150, expected_answer="a", source_excerpt="...", active=True
            )
        )
        db.add(
            Question(
                conversation_id=1, session_id=seeded_session, text="w" * 200, expected_answer="a", source_excerpt="...", active=False
            )
        )  # already inactive -- must not be touched
        db.commit()

    counts = shorten_active_questions()

    assert counts["checked"] == 2  # the inactive one is excluded
    assert counts["already_ok"] == 1
    assert counts["shortened"] == 1
    assert counts["dropped"] == 0


def test_lecture_study_session_auto_approved_with_questions(db_engine, seeded_session, monkeypatch):
    _patch(monkeypatch, db_engine, _FakeProvider("lecture_study", n_questions=3))
    questions = generate_for_session(seeded_session, n=3)
    assert len(questions) == 3
    with DBSession(db_engine) as db:
        session = db.get(SessionModel, seeded_session)
        assert session.session_type == "lecture_study"
        assert session.approval_status == "approved"
        assert len(db.exec(select(Question).where(Question.session_id == seeded_session)).all()) == 3


def test_substantive_discussion_session_pending_with_questions(db_engine, seeded_session, monkeypatch):
    _patch(monkeypatch, db_engine, _FakeProvider("substantive_discussion", n_questions=2))
    questions = generate_for_session(seeded_session, n=2)
    assert len(questions) == 2
    with DBSession(db_engine) as db:
        session = db.get(SessionModel, seeded_session)
        assert session.approval_status == "pending"


@pytest.mark.parametrize("session_type", ["casual", "personal"])
def test_casual_and_personal_sessions_get_no_questions(db_engine, seeded_session, monkeypatch, session_type):
    _patch(monkeypatch, db_engine, _FakeProvider(session_type, n_questions=5))
    questions = generate_for_session(seeded_session, n=5)
    assert questions == []
    with DBSession(db_engine) as db:
        session = db.get(SessionModel, seeded_session)
        assert session.session_type == session_type
        assert session.approval_status == "none"
        assert db.exec(select(Question).where(Question.session_id == seeded_session)).all() == []


def test_list_pending_only_returns_pending_sessions(db_engine, seeded_session, monkeypatch):
    _patch(monkeypatch, db_engine, _FakeProvider("substantive_discussion"))
    generate_for_session(seeded_session, n=1)
    monkeypatch.setattr("recall.questions.get_session", lambda: DBSession(db_engine))
    pending = list_pending()
    assert len(pending) == 1
    assert pending[0].id == seeded_session


def test_set_approval_transitions_pending_to_approved(db_engine, seeded_session, monkeypatch):
    _patch(monkeypatch, db_engine, _FakeProvider("substantive_discussion"))
    generate_for_session(seeded_session, n=1)
    monkeypatch.setattr("recall.questions.get_session", lambda: DBSession(db_engine))
    set_approval(seeded_session, "approved")
    with DBSession(db_engine) as db:
        assert db.get(SessionModel, seeded_session).approval_status == "approved"


def test_set_approval_rejects_non_pending_session(db_engine, seeded_session, monkeypatch):
    _patch(monkeypatch, db_engine, _FakeProvider("lecture_study"))  # -> approved, not pending
    generate_for_session(seeded_session, n=1)
    monkeypatch.setattr("recall.questions.get_session", lambda: DBSession(db_engine))
    with pytest.raises(ValueError):
        set_approval(seeded_session, "approved")


def _u(id_: int, text: str, offset_s: float) -> Utterance:
    return Utterance(id=id_, conversation_id=1, conversation_uuid="x", text=text, spoken_at=BASE + timedelta(seconds=offset_s))


def test_excluded_session_refuses_to_generate(db_engine, seeded_session, monkeypatch):
    _patch(monkeypatch, db_engine, _FakeProvider("lecture_study"))
    monkeypatch.setattr("recall.questions.load_excluded_sessions", lambda: {seeded_session})
    with pytest.raises(ValueError, match="excluded_sessions"):
        generate_for_session(seeded_session, n=1)


def test_already_classified_session_refuses_regeneration(db_engine, seeded_session, monkeypatch):
    _patch(monkeypatch, db_engine, _FakeProvider("lecture_study", n_questions=2))
    generate_for_session(seeded_session, n=2)
    with pytest.raises(ValueError, match="already classified"):
        generate_for_session(seeded_session, n=2)


def test_force_allows_regeneration_of_classified_session(db_engine, seeded_session, monkeypatch):
    _patch(monkeypatch, db_engine, _FakeProvider("lecture_study", n_questions=2))
    generate_for_session(seeded_session, n=2)
    questions = generate_for_session(seeded_session, n=2, force=True)
    assert len(questions) == 2


def test_answer_window_utterances_excluded_from_transcript(db_engine, monkeypatch):
    captured_transcript = {}

    class _CapturingProvider(_FakeProvider):
        def generate_questions(self, transcript, n, target_language=None):
            captured_transcript["text"] = transcript
            return super().generate_questions(transcript, n, target_language)

    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        db.add_all(
            [
                _u(1, "self fed answer text", 0),
                _u(2, "genuine new lecture content", 60),
            ]
        )
        session = SessionModel(start_at=BASE, end_at=BASE + timedelta(seconds=60), conversation_ids=[1])
        db.add(session)
        db.commit()
        db.refresh(session)
        session_id = session.id

        q = Question(conversation_id=1, text="Q", expected_answer="A", source_excerpt="...")
        db.add(q)
        db.commit()
        db.refresh(q)
        dispatch = Dispatch(question_id=q.id, bee_todo_id=1, alarm_at=BASE)
        db.add(dispatch)
        db.commit()
        db.refresh(dispatch)
        db.add(Answer(dispatch_id=dispatch.id, raw_text="self fed answer text", utterance_ids=[1]))
        db.commit()

    _patch(monkeypatch, db_engine, _CapturingProvider("lecture_study", n_questions=1))
    generate_for_session(session_id, n=1)

    assert "self fed answer text" not in captured_transcript["text"]
    assert "genuine new lecture content" in captured_transcript["text"]


def test_set_type_override_changes_classification(db_engine, seeded_session, monkeypatch):
    monkeypatch.setattr("recall.questions.get_session", lambda: DBSession(db_engine))
    retired = set_type(seeded_session, "casual")
    assert retired == 0  # no questions existed yet
    with DBSession(db_engine) as db:
        session = db.get(SessionModel, seeded_session)
        assert session.session_type == "casual"
        assert session.approval_status == "none"


def test_set_type_to_casual_retires_existing_questions(db_engine, seeded_session, monkeypatch):
    _patch(monkeypatch, db_engine, _FakeProvider("lecture_study", n_questions=3))
    generate_for_session(seeded_session, n=3)
    monkeypatch.setattr("recall.questions.get_session", lambda: DBSession(db_engine))

    retired = set_type(seeded_session, "personal")

    assert retired == 3
    with DBSession(db_engine) as db:
        questions = db.exec(select(Question).where(Question.session_id == seeded_session)).all()
        assert len(questions) == 3  # never deleted
        assert all(not q.active for q in questions)


def test_set_type_rejects_invalid_type(db_engine, seeded_session, monkeypatch):
    monkeypatch.setattr("recall.questions.get_session", lambda: DBSession(db_engine))
    with pytest.raises(ValueError):
        set_type(seeded_session, "not_a_real_type")


def test_reject_question_marks_inactive_not_deleted(db_engine, seeded_session, monkeypatch):
    _patch(monkeypatch, db_engine, _FakeProvider("lecture_study", n_questions=2))
    questions = generate_for_session(seeded_session, n=2)
    monkeypatch.setattr("recall.questions.get_session", lambda: DBSession(db_engine))

    reject_question(questions[0].id)

    with DBSession(db_engine) as db:
        q = db.get(Question, questions[0].id)
        assert q is not None  # not deleted
        assert q.active is False
        other = db.get(Question, questions[1].id)
        assert other.active is True


def test_retire_sessions_marks_all_active_questions_inactive(db_engine, seeded_session, monkeypatch):
    _patch(monkeypatch, db_engine, _FakeProvider("lecture_study", n_questions=3))
    generate_for_session(seeded_session, n=3)
    monkeypatch.setattr("recall.questions.get_session", lambda: DBSession(db_engine))

    retired = retire_sessions([seeded_session])

    assert retired == 3
    with DBSession(db_engine) as db:
        questions = db.exec(select(Question).where(Question.session_id == seeded_session)).all()
        assert len(questions) == 3
        assert all(not q.active for q in questions)


def test_exact_substring_match_single_utterance():
    utterances = [
        _u(1, "Before that it was the Roman Kingdom.", 0),
        _u(2, "Then came the Republic.", 10),
    ]
    start, end = locate_source_window(utterances, "the Roman Kingdom")
    assert start == utterances[0].spoken_at
    assert end == utterances[0].spoken_at


def test_exact_substring_spanning_multiple_utterances_uses_first_and_last_match():
    utterances = [
        _u(1, "the Roman Kingdom was first", 0),
        _u(2, "unrelated aside", 10),
        _u(3, "the Roman Kingdom fell eventually", 20),
    ]
    start, end = locate_source_window(utterances, "the Roman Kingdom")
    assert start == utterances[0].spoken_at
    assert end == utterances[2].spoken_at


def test_fuzzy_fallback_when_no_exact_match():
    utterances = [_u(1, "Before the Roman Empire there was the Roman Republic", 0)]
    # slightly mangled quote (STT-style) that doesn't appear verbatim
    start, end = locate_source_window(utterances, "before the roman empire there was the roman republc")
    assert start == utterances[0].spoken_at
    assert end == utterances[0].spoken_at


def test_no_match_returns_none_none():
    utterances = [_u(1, "completely unrelated content", 0)]
    start, end = locate_source_window(utterances, "something totally different about spacecraft")
    assert start is None
    assert end is None


def test_empty_excerpt_returns_none_none():
    utterances = [_u(1, "some text", 0)]
    start, end = locate_source_window(utterances, "")
    assert start is None
    assert end is None
