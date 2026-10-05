from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine, select

from recall.db import Answer, Conversation, Dispatch, FeedbackTodo, Question
from recall.db import Session as SessionModel
from recall.db import SessionRun
from recall.llm.base import GradeResult, LLMProvider

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class _FakeProvider(LLMProvider):
    def __init__(self, result: GradeResult):
        self.result = result

    def generate_questions(self, transcript, n, target_language=None):
        raise NotImplementedError

    def grade_answer(self, question, spoken_answer):
        return self.result

    def shorten_question(self, text, max_chars):
        raise NotImplementedError


class _FakeBeeClient:
    def __init__(self, store: dict, next_id_box: list):
        self.store = store
        self.next_id_box = next_id_box

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def create_todo(self, text, alarm_at=None):
        new_id = self.next_id_box[0]
        self.next_id_box[0] += 1
        self.store[new_id] = {"text": text, "alarm_at": alarm_at, "deleted": False}
        return {"id": new_id}

    def delete_todo(self, todo_id):
        if todo_id not in self.store:
            raise RuntimeError(f"attempted to delete unknown todo id {todo_id}")
        self.store[todo_id]["deleted"] = True
        return {"success": True}


class _FakeBeeProxy:
    def __init__(self, port=None):
        self.base_url = "http://fake"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


@pytest.fixture
def bee_store():
    return {}, [1000]


@pytest.fixture
def db_engine(tmp_path, monkeypatch, bee_store):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("recall.session.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.grading.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.scoring.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("time.sleep", lambda s: None)

    store, next_id_box = bee_store
    monkeypatch.setattr("recall.session.BeeProxy", lambda port=None: _FakeBeeProxy())
    monkeypatch.setattr("recall.session.BeeClient", lambda base_url: _FakeBeeClient(store, next_id_box))
    return engine


@pytest.fixture(autouse=True)
def patch_provider(monkeypatch):
    def _patch(result: GradeResult):
        monkeypatch.setattr("recall.grading.get_provider", lambda: _FakeProvider(result))

    return _patch


def _approved_session(db, session_id=1):
    db.add(SessionModel(id=session_id, start_at=BASE, end_at=BASE, conversation_ids=[1], approval_status="approved"))


def _question(db, qid, session_id=1, text="Q", rubric=None):
    q = Question(id=qid, conversation_id=1, session_id=session_id, text=text, expected_answer="A", rubric=rubric or ["a", "b"], source_excerpt="...")
    db.add(q)
    return q


def _seed_questions(engine, n: int, session_id=1):
    with DBSession(engine) as db:
        db.add(Conversation(id=1))
        _approved_session(db, session_id)
        for i in range(1, n + 1):
            _question(db, i, session_id=session_id, text=f"Question {i}?")
        db.commit()


def test_question_todo_text_uses_full_prefix_when_it_fits():
    from recall.session import QUESTION_PREFIX, TODO_TEXT_MAX_CHARS, question_todo_text

    text = question_todo_text("Short question?")
    assert text == f"{QUESTION_PREFIX}Short question?"
    assert len(text) <= TODO_TEXT_MAX_CHARS


def test_question_todo_text_falls_back_to_short_prefix_when_too_long():
    from recall.session import QUESTION_PREFIX_SHORT, TODO_TEXT_MAX_CHARS, question_todo_text

    long_question = "A" * 90  # full prefix (62 chars) + this would exceed 120
    text = question_todo_text(long_question)
    assert text == f"{QUESTION_PREFIX_SHORT}{long_question}"
    assert len(text) <= TODO_TEXT_MAX_CHARS

    # At the ~100-char ceiling recall.questions already allows, even the
    # short prefix (23 chars) can still push past 120 -- known limitation,
    # reported separately rather than silently truncating the question.
    near_max_question = "A" * 100
    assert len(f"{QUESTION_PREFIX_SHORT}{near_max_question}") > TODO_TEXT_MAX_CHARS


def test_start_session_returns_none_when_nothing_due(db_engine):
    from recall.session import start_session

    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        db.commit()

    assert start_session("tick", now=BASE) is None


def test_start_session_dispatches_first_due_question(db_engine, bee_store):
    from recall.session import start_session

    _seed_questions(db_engine, 3)
    store, _ = bee_store

    run = start_session("spoken", now=BASE)

    assert run is not None
    assert run.status == "active"
    assert run.trigger == "spoken"
    assert run.questions_asked == 1
    with DBSession(db_engine) as db:
        dispatches = db.exec(select(Dispatch)).all()
        assert len(dispatches) == 1
        assert dispatches[0].session_run_id == run.id
    assert len(store) == 1  # the question todo


def test_start_session_refuses_when_already_active(db_engine):
    from recall.session import start_session

    _seed_questions(db_engine, 3)
    start_session("tick", now=BASE)
    second = start_session("tick", now=BASE + timedelta(seconds=1))

    assert second is None
    with DBSession(db_engine) as db:
        assert len(db.exec(select(SessionRun)).all()) == 1


def _answer_open_dispatch(db_engine, bee_store, verdict, quality, rubric_hit=None, raw_text="a reasonably long spoken answer with plenty of extra words included here to be safe"):
    """Simulates a spoken answer arriving for whatever dispatch is
    currently open, by seeding a matching utterance."""
    from recall.db import Utterance

    with DBSession(db_engine) as db:
        open_d = db.exec(select(Dispatch).where(Dispatch.status == "pending")).first()
        db.add(Utterance(id=open_d.id * 1000, conversation_id=1, conversation_uuid="x", text=raw_text, spoken_at=open_d.alarm_at + timedelta(seconds=5)))
        db.commit()


READY_AFTER_ANSWER_S = 25  # utterance seeded at alarm+5s; 15s quiet => ready at alarm+20s
DEFER_AFTER_S = 125  # just past the 2-min no-speech hard cap


def test_quota_hit_ends_session_completed_and_posts_report(db_engine, bee_store, patch_provider, monkeypatch):
    from dataclasses import replace

    import recall.session as session_module
    from recall.session import advance, start_session

    # settings is a frozen dataclass -- swap the whole module-level instance
    # recall.session reads, rather than trying to mutate a frozen field.
    monkeypatch.setattr(session_module, "settings", replace(session_module.settings, daily_quota=2))

    _seed_questions(db_engine, 3)
    patch_provider(GradeResult(verdict="correct", rubric_points_hit=["a", "b"], quality=5, feedback="Nice."))

    now = BASE

    run = start_session("tick", now=now)
    ready_at = now + timedelta(seconds=READY_AFTER_ANSWER_S)

    _answer_open_dispatch(db_engine, bee_store, "correct", 5)
    result = advance(now=ready_at)
    assert result["event"] == "next_question_dispatched"

    _answer_open_dispatch(db_engine, bee_store, "correct", 5)
    result = advance(now=ready_at + timedelta(seconds=READY_AFTER_ANSWER_S))
    assert result["event"] == "session_completed_quota"

    with DBSession(db_engine) as db:
        run_row = db.get(SessionRun, run.id)
        assert run_row.status == "completed"
        assert run_row.questions_asked == 2
        assert run_row.questions_answered == 2

    store, _ = bee_store
    report_texts = [v["text"] for v in store.values() if "correct today" in v["text"]]
    assert len(report_texts) == 1
    assert "2 out of 2 correct today" in report_texts[0]
    assert "still processing" not in report_texts[0]


def test_single_deferred_question_does_not_end_session(db_engine, bee_store, patch_provider):
    """Phase 5E Step 3: one deferred (no-speech-by-2min) dispatch just
    moves on to the next question -- it takes TWO consecutive deferred
    dispatches to pause the session, not one."""
    from recall.session import advance, start_session

    _seed_questions(db_engine, 3)

    now = BASE
    run = start_session("tick", now=now)
    defer_at = now + timedelta(seconds=DEFER_AFTER_S)

    # No utterance seeded -- nothing arrives within the 2-min hard cap.
    result = advance(now=defer_at)

    assert result["event"] == "deferred_next_question_dispatched"
    with DBSession(db_engine) as db:
        run_row = db.get(SessionRun, run.id)
        assert run_row.status == "active"
        assert run_row.questions_asked == 2
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        assert d1.status == "processing"
        # Not graded yet -- no Answer row at all, no SM-2 touched.
        assert db.exec(select(Answer).where(Answer.dispatch_id == d1.id)).first() is None
        q1 = db.get(Question, 1)
        assert q1.active is True


def test_two_consecutive_deferred_pauses_session_no_penalty(db_engine, bee_store, patch_provider):
    from recall.session import advance, start_session

    _seed_questions(db_engine, 3)

    now = BASE
    run = start_session("tick", now=now)
    defer_at = now + timedelta(seconds=DEFER_AFTER_S)

    result = advance(now=defer_at)
    assert result["event"] == "deferred_next_question_dispatched"

    result = advance(now=defer_at + timedelta(seconds=DEFER_AFTER_S))
    assert result["event"] == "session_paused_two_deferred"

    with DBSession(db_engine) as db:
        run_row = db.get(SessionRun, run.id)
        assert run_row.status == "paused"
        assert run_row.questions_asked == 2
        assert run_row.questions_answered == 0
        for qid in (1, 2):
            d = db.exec(select(Dispatch).where(Dispatch.question_id == qid)).first()
            assert d.status == "processing"

    store, _ = bee_store
    report_texts = [v["text"] for v in store.values() if "correct today" in v["text"]]
    assert len(report_texts) == 1
    assert "0 out of 2 correct today" in report_texts[0]
    assert "2 still processing" in report_texts[0]


def test_deferred_dispatch_graded_later_from_final_transcript(db_engine, bee_store, patch_provider):
    """Phase 5E Step 3: a deferred dispatch is graded in the background
    once the final transcript shows an utterance landing inside
    [this alarm_at, next alarm_at) -- matched by spoken_at, no feedback
    todo posted (dashboard-only)."""
    from recall.db import Utterance
    from recall.session import advance, grade_pending_dispatches, start_session

    _seed_questions(db_engine, 3)
    patch_provider(GradeResult(verdict="partial", rubric_points_hit=["a"], quality=3, feedback="Missed b."))

    now = BASE
    start_session("tick", now=now)
    defer_at = now + timedelta(seconds=DEFER_AFTER_S)
    result = advance(now=defer_at)  # question 1 deferred, question 2 dispatched
    assert result["event"] == "deferred_next_question_dispatched"

    store_before, _ = bee_store
    todos_before = len(store_before)

    with DBSession(db_engine) as db:
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        # A late-arriving final-transcript utterance, inside [d1.alarm_at, d2.alarm_at).
        db.add(Utterance(id=99001, conversation_id=1, conversation_uuid="x", text="a late answer that finally came through with plenty of extra words to be safe", spoken_at=d1.alarm_at + timedelta(seconds=30)))
        db.commit()

    results = grade_pending_dispatches(now=defer_at + timedelta(seconds=60))
    assert len(results) == 1
    assert results[0]["question_id"] == 1
    assert results[0]["verdict"] == "partial"

    with DBSession(db_engine) as db:
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        assert d1.status == "answered"
        answer = db.exec(select(Answer).where(Answer.dispatch_id == d1.id)).first()
        assert answer.verdict == "partial"
        run_row = db.exec(select(SessionRun)).first()
        assert run_row.questions_answered == 1  # updated even though the session already moved on

    # No new feedback todo posted for the deferred grade -- dashboard-only.
    store_after, _ = bee_store
    assert len(store_after) == todos_before


def test_grade_pending_dispatches_is_a_noop_when_nothing_new_arrived(db_engine, bee_store, patch_provider):
    from recall.session import advance, grade_pending_dispatches, start_session

    _seed_questions(db_engine, 3)
    now = BASE
    start_session("tick", now=now)
    defer_at = now + timedelta(seconds=DEFER_AFTER_S)
    advance(now=defer_at)

    results = grade_pending_dispatches(now=defer_at + timedelta(seconds=30))
    assert results == []
    with DBSession(db_engine) as db:
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        assert d1.status == "processing"  # still pending resolution


def test_restart_mid_session_resumes_same_open_dispatch(db_engine, bee_store, patch_provider):
    """Simulates a daemon restart: start_session() must not be callable
    again (one session active), and advance() must find the SAME open
    dispatch rather than losing or duplicating it."""
    from recall.session import has_active_session, open_dispatch_for, start_session

    _seed_questions(db_engine, 3)
    run = start_session("tick", now=BASE)

    with DBSession(db_engine) as db:
        original_dispatch = open_dispatch_for(db, run.id)
        assert original_dispatch is not None
        original_dispatch_id = original_dispatch.id

    # "Restart": nothing in memory survives, but the DB state does.
    with DBSession(db_engine) as db:
        resumed_run = has_active_session(db)
        assert resumed_run.id == run.id
        resumed_dispatch = open_dispatch_for(db, resumed_run.id)
        assert resumed_dispatch.id == original_dispatch_id


def test_recover_defers_stale_dispatch_and_pauses_session(db_engine, bee_store, patch_provider):
    """Phase 5E: recover() no longer forces an immediate no_answer -- a
    sleep/wake gap can be long, and a real answer might just be slow to
    transcribe (Phase 5D). It defers instead (graded later from the final
    transcript, same as any other deferred dispatch) and pauses the
    session, no penalty."""
    from recall.session import recover, start_session

    _seed_questions(db_engine, 3)
    now = BASE
    run = start_session("tick", now=now)

    # Simulate a laptop-sleep gap: recover() is called much later, well
    # past the answer window, even though nothing ever polled in between.
    much_later = now + timedelta(hours=6)
    result = recover(now=much_later)

    assert result["event"] == "recovered_stale_session"
    with DBSession(db_engine) as db:
        run_row = db.get(SessionRun, run.id)
        assert run_row.status == "paused"
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        assert d1.status == "processing"
        assert db.exec(select(Answer)).first() is None  # not graded yet -- deferred


def test_recover_is_a_noop_when_window_has_not_passed_yet(db_engine, bee_store, patch_provider):
    from recall.session import recover, start_session

    _seed_questions(db_engine, 3)
    now = BASE
    start_session("tick", now=now)

    result = recover(now=now + timedelta(seconds=30))

    assert result is None
    with DBSession(db_engine) as db:
        run_row = db.exec(select(SessionRun)).first()
        assert run_row.status == "active"


def test_recover_is_a_noop_when_no_active_session(db_engine):
    from recall.session import recover

    assert recover(now=BASE) is None


def test_feedback_text_is_plain_words_no_symbols(db_engine, bee_store, patch_provider):
    """AGENT.md copy rule: no emoji, no checkmarks -- Bee rewrites them
    away before the wearer ever sees them."""
    from recall.session import advance, start_session

    _seed_questions(db_engine, 1)
    patch_provider(GradeResult(verdict="partial", rubric_points_hit=["a"], quality=3, feedback="Missed the second point."))

    now = BASE
    start_session("tick", now=now)
    _answer_open_dispatch(db_engine, bee_store, "partial", 3)
    advance(now=now + timedelta(seconds=331))

    store, _ = bee_store
    # Feedback todo: not deleted (unlike the question todo, which was),
    # not the question text itself, not the end-of-session report.
    feedback_texts = [
        v["text"]
        for v in store.values()
        if v["text"].startswith("Recall:") and not v["deleted"] and "correct today" not in v["text"] and "Question 1?" not in v["text"]
    ]
    assert len(feedback_texts) == 1
    text = feedback_texts[0]
    assert "✓" not in text
    assert "✗" not in text
    assert "/" not in text  # no "1/2" shorthand
    for ch in text:
        assert ord(ch) < 0x1F000  # no emoji range characters
