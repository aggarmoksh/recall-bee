from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from recall.db import Conversation, Question
from recall.db import Session as SessionModel
from recall.run import SESSION_CLOSED_GAP_MINUTES, find_closed_unclassified_sessions

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


# --- spoken-start detection buffer (_on_new_utterance / fast_tick) ---


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
        self.store[new_id] = {"text": text, "completed": False, "deleted": False}
        return {"id": new_id}

    def get_todo(self, todo_id):
        return {"id": todo_id, "completed": self.store[todo_id]["completed"]}

    def delete_todo(self, todo_id):
        self.store[todo_id]["deleted"] = True


@pytest.fixture
def reset_run_state(monkeypatch):
    """_on_new_utterance/_pending_spoken_start are module-level state --
    reset between tests so they don't leak across test runs."""
    import recall.run as run_module

    run_module._recent_utterances.clear()
    run_module._pending_spoken_start = None
    yield
    run_module._recent_utterances.clear()
    run_module._pending_spoken_start = None


@pytest.fixture
def run_db_engine(tmp_path, monkeypatch, reset_run_state):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("recall.run.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.session.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.ready_todo.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.scoring.get_session", lambda: DBSession(engine))
    return engine


def test_on_new_utterance_single_match_flags_pending(reset_run_state):
    from recall.run import _on_new_utterance
    import recall.run as run_module

    now = datetime.now(UTC)
    _on_new_utterance(1, "Hey Bee, let's do a Recall session, I'm ready for my questions.", now)

    assert run_module._pending_spoken_start == [1]


def test_on_new_utterance_split_across_two_utterances_flags_pending(reset_run_state):
    """Reproduces the Phase 5B pattern where STT can split the words
    themselves across chunks, not just the sentence -- "recall" lands in
    one utterance, "session" only arrives in the next."""
    from recall.run import _on_new_utterance
    import recall.run as run_module

    now = datetime.now(UTC)
    _on_new_utterance(1, "Hey Bee, let's do a Recall", now)
    assert run_module._pending_spoken_start is None  # "session" hasn't arrived yet
    _on_new_utterance(2, "session now, I'm ready for my questions.", now + timedelta(seconds=2))

    assert run_module._pending_spoken_start == [1, 2]


def test_on_new_utterance_old_entries_fall_out_of_the_window(reset_run_state, monkeypatch):
    """Phase 5E fix: the window is keyed on ARRIVAL time (when this
    callback actually runs), not `spoken_at` -- with realtime-transcription
    latency routinely 25-40s+ (Phase 5D), keying on spoken_at pruned an
    utterance from the buffer the instant it arrived, before detection
    ever ran on it (found live, Step 6). Simulated here via a fake clock,
    since a real wall-clock gap is what the window now actually tracks."""
    import recall.run as run_module
    from recall.run import START_PHRASE_WINDOW_S, _on_new_utterance

    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock = {"now": base}

    class _FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(run_module, "datetime", _FakeDateTime)

    _on_new_utterance(1, "unrelated chatter about recall", base)
    clock["now"] = base + timedelta(seconds=START_PHRASE_WINDOW_S + 5)
    _on_new_utterance(2, "something about a session later", clock["now"])

    # "recall" arrived long enough ago (by wall clock) to fall out of the window
    assert run_module._pending_spoken_start is None


def test_on_new_utterance_detects_even_when_spoken_at_is_already_old(reset_run_state):
    """The flip side of the fix above: an utterance whose spoken_at is
    already minutes in the past (slow transcription) must still be
    detected, since windowing no longer depends on spoken_at at all."""
    from recall.run import _on_new_utterance
    import recall.run as run_module

    long_ago = datetime.now(UTC) - timedelta(minutes=5)
    _on_new_utterance(1, "Hey Bee, let's do a Recall session, I'm ready for my questions.", long_ago)

    assert run_module._pending_spoken_start == [1]


def test_on_new_utterance_unrelated_speech_never_flags(reset_run_state):
    from recall.run import _on_new_utterance
    import recall.run as run_module

    _on_new_utterance(1, "I need to buy groceries later", datetime.now(UTC))
    assert run_module._pending_spoken_start is None


def test_react_to_trigger_posts_catch_up_todo_when_nothing_due(run_db_engine, monkeypatch):
    from recall.run import _react_to_trigger

    store, next_id_box = {}, [1]
    client = _FakeBeeClient(store, next_id_box)
    with DBSession(run_db_engine) as db:
        db.add(Conversation(id=1))
        db.commit()

    _react_to_trigger(client, "tick", datetime.now(UTC))

    assert len(store) == 1
    assert "nothing due" in list(store.values())[0]["text"]


def test_react_to_trigger_starts_session_when_due(run_db_engine, monkeypatch):
    from recall.run import _react_to_trigger

    store, next_id_box = {}, [1]
    client = _FakeBeeClient(store, next_id_box)
    monkeypatch.setattr("recall.session.BeeProxy", lambda port=None: _FakeProxy())
    monkeypatch.setattr("recall.session.BeeClient", lambda base_url: client)

    with DBSession(run_db_engine) as db:
        db.add(Conversation(id=1))
        db.add(SessionModel(id=1, start_at=BASE, end_at=BASE, conversation_ids=[1], approval_status="approved"))
        db.add(Question(id=1, conversation_id=1, session_id=1, text="Q", expected_answer="A", rubric=["a"], source_excerpt="..."))
        db.commit()

    _react_to_trigger(client, "spoken", datetime.now(UTC))

    # one todo for the question itself (start_session dispatches immediately)
    assert len(store) == 1
    assert "nothing due" not in list(store.values())[0]["text"]


class _FakeProxy:
    def __init__(self, port=None):
        self.base_url = "http://fake"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass
