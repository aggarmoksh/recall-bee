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
    window_closed = now + timedelta(seconds=331)

    _answer_open_dispatch(db_engine, bee_store, "correct", 5)
    result = advance(now=window_closed)
    assert result["event"] == "next_question_dispatched"

    _answer_open_dispatch(db_engine, bee_store, "correct", 5)
    result = advance(now=window_closed + timedelta(seconds=400))
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


def test_no_answer_ends_session_with_no_penalty(db_engine, bee_store, patch_provider):
    from recall.session import advance, start_session

    _seed_questions(db_engine, 3)

    now = BASE
    run = start_session("tick", now=now)
    window_closed = now + timedelta(seconds=331)

    # No utterance seeded -- the window closes with nothing captured.
    result = advance(now=window_closed)

    assert result["event"] == "session_ended_no_answer"
    with DBSession(db_engine) as db:
        run_row = db.get(SessionRun, run.id)
        assert run_row.status == "ended_no_answer"
        assert run_row.questions_answered == 0
        answer = db.exec(select(Answer)).first()
        assert answer.verdict == "no_answer"
        # Question 1 stays due (not retired, no SM-2 penalty) -- it has no
        # ReviewState row at all since no_answer never creates/updates one.
        from recall.db import ReviewState

        assert db.exec(select(ReviewState).where(ReviewState.question_id == 1)).first() is None
        q1 = db.get(Question, 1)
        assert q1.active is True


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


def test_recover_forces_stale_dispatch_to_no_answer_and_ends_session(db_engine, bee_store, patch_provider):
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
        assert run_row.status == "ended_no_answer"
        answer = db.exec(select(Answer)).first()
        assert answer.verdict == "no_answer"
        assert answer.raw_text == ""


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
