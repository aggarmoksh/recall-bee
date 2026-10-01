from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine, select

from recall.db import Answer, Conversation, Dispatch, Question
from recall.db import Session as SessionModel
from recall.db import StingTodo, Utterance

TZ = ZoneInfo("America/Los_Angeles")


def _local(y, mo, d, h, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=TZ).astimezone(UTC)


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
    return {}, [2000]


@pytest.fixture
def db_engine(tmp_path, monkeypatch, bee_store):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("recall.stings.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.scoring.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.session.get_session", lambda: DBSession(engine))

    store, next_id_box = bee_store
    monkeypatch.setattr("recall.stings.BeeProxy", lambda port=None: _FakeBeeProxy())
    monkeypatch.setattr("recall.stings.BeeClient", lambda base_url: _FakeBeeClient(store, next_id_box))
    return engine


def _seed_due_question(engine):
    with DBSession(engine) as db:
        db.add(Conversation(id=1))
        db.add(SessionModel(id=1, start_at=_local(2026, 1, 1, 10), end_at=_local(2026, 1, 1, 10), conversation_ids=[1], approval_status="approved"))
        db.add(Question(id=1, conversation_id=1, session_id=1, text="Q", expected_answer="A", rubric=["a"], source_excerpt="..."))
        db.commit()


def test_no_sting_outside_active_window(db_engine, bee_store):
    from recall.stings import maybe_send_stings

    _seed_due_question(db_engine)
    # default ACTIVE_WINDOW 18:00-00:00 -- noon is outside it
    events = maybe_send_stings(now=_local(2026, 1, 1, 12))
    assert events == []
    assert bee_store[0] == {}


def test_sting_sent_at_window_start(db_engine, bee_store):
    from recall.stings import maybe_send_stings

    _seed_due_question(db_engine)
    events = maybe_send_stings(now=_local(2026, 1, 1, 18, 0))

    assert len(events) == 1
    assert events[0]["slot"] == "start"
    store, _ = bee_store
    assert len(store) == 1
    text = list(store.values())[0]["text"]
    assert "buzzing for you" in text
    assert "Hey Bee" in text
    with DBSession(db_engine) as db:
        rows = db.exec(select(StingTodo)).all()
        assert len(rows) == 1
        assert rows[0].slot == "start"
        assert rows[0].local_date == "2026-01-01"


def test_same_slot_not_sent_twice(db_engine, bee_store):
    from recall.stings import maybe_send_stings

    _seed_due_question(db_engine)
    maybe_send_stings(now=_local(2026, 1, 1, 18, 0))
    events = maybe_send_stings(now=_local(2026, 1, 1, 18, 5))

    assert events == []
    store, _ = bee_store
    assert len(store) == 1  # still just the one


def test_middle_slot_fires_at_window_midpoint(db_engine, bee_store):
    from recall.stings import maybe_send_stings

    _seed_due_question(db_engine)
    # window 18:00-00:00 -> midpoint 21:00
    events = maybe_send_stings(now=_local(2026, 1, 1, 21, 0))

    assert len(events) == 1
    assert events[0]["slot"] == "middle"


def test_end_slot_fires_90min_before_window_end(db_engine, bee_store):
    from recall.stings import maybe_send_stings

    _seed_due_question(db_engine)
    # window ends 00:00 -> end slot at 22:30
    events = maybe_send_stings(now=_local(2026, 1, 1, 22, 30))

    assert len(events) == 1
    assert events[0]["slot"] == "end"


def test_no_sting_when_daily_goal_already_met(db_engine, bee_store, monkeypatch):
    from dataclasses import replace

    import recall.stings as stings_module
    from recall.stings import maybe_send_stings

    monkeypatch.setattr(stings_module, "settings", replace(stings_module.settings, daily_quota=1))

    _seed_due_question(db_engine)
    with DBSession(db_engine) as db:
        d = Dispatch(question_id=1, bee_todo_id=1, alarm_at=_local(2026, 1, 1, 17), dispatched_at=_local(2026, 1, 1, 17), status="answered")
        db.add(d)
        db.commit()
        db.refresh(d)
        db.add(Answer(dispatch_id=d.id, raw_text="answer", utterance_ids=[], verdict="correct", quality=5, matched_at=_local(2026, 1, 1, 17, 1)))
        db.commit()

    events = maybe_send_stings(now=_local(2026, 1, 1, 18, 0))
    assert events == []


def test_sting_held_when_recently_busy(db_engine, bee_store):
    from recall.stings import maybe_send_stings

    _seed_due_question(db_engine)
    now = _local(2026, 1, 1, 18, 0)
    with DBSession(db_engine) as db:
        db.add(Utterance(id=1, conversation_id=1, conversation_uuid="x", text="ambient speech", spoken_at=now - timedelta(seconds=60)))
        db.commit()

    events = maybe_send_stings(now=now)
    assert events == []
    assert bee_store[0] == {}


def test_sting_not_held_if_busy_window_has_passed(db_engine, bee_store):
    from recall.stings import maybe_send_stings

    _seed_due_question(db_engine)
    now = _local(2026, 1, 1, 18, 0)
    with DBSession(db_engine) as db:
        db.add(Utterance(id=1, conversation_id=1, conversation_uuid="x", text="ambient speech", spoken_at=now - timedelta(seconds=200)))
        db.commit()

    events = maybe_send_stings(now=now)
    assert len(events) == 1


def test_max_stings_cap_blocks_further_slot_stings(db_engine, bee_store, monkeypatch):
    import recall.stings as stings_module
    from recall.stings import maybe_send_stings

    monkeypatch.setattr(stings_module, "settings", replace(stings_module.settings, max_stings=1))

    _seed_due_question(db_engine)
    maybe_send_stings(now=_local(2026, 1, 1, 18, 0))  # uses up the 1 slot
    events = maybe_send_stings(now=_local(2026, 1, 1, 21, 0))  # middle slot time

    assert events == []
    store, _ = bee_store
    assert len(store) == 1


def test_no_sting_when_nothing_due(db_engine, bee_store):
    from recall.stings import maybe_send_stings

    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        db.commit()

    events = maybe_send_stings(now=_local(2026, 1, 1, 18, 0))
    assert events == []


def test_wrapping_window_keys_both_sides_to_the_same_day(db_engine, bee_store, monkeypatch):
    """ACTIVE_WINDOW=13:00-03:00 (operator's own real window): 1am local
    the next calendar date is still part of the PREVIOUS day's window."""
    import recall.stings as stings_module

    monkeypatch.setattr(stings_module, "settings", replace(stings_module.settings, active_window="13:00-03:00"))
    from recall.stings import maybe_send_stings

    _seed_due_question(db_engine)
    # 1am Jan 2 local -- still inside the window that started 13:00 Jan 1
    events = maybe_send_stings(now=_local(2026, 1, 2, 1, 0))
    assert len(events) == 1

    with DBSession(db_engine) as db:
        rows = db.exec(select(StingTodo)).all()
        assert rows[0].local_date == "2026-01-01"


def test_post_lecture_sting_does_not_count_against_max_stings(db_engine, bee_store, monkeypatch):
    import recall.stings as stings_module

    monkeypatch.setattr(stings_module, "settings", replace(stings_module.settings, max_stings=1))
    from recall.stings import maybe_send_stings, send_post_lecture_sting

    _seed_due_question(db_engine)
    maybe_send_stings(now=_local(2026, 1, 1, 18, 0))  # uses the 1 slot cap
    result = send_post_lecture_sting(now=_local(2026, 1, 1, 18, 10))

    assert result is not None
    assert result["slot"] == "post_lecture"
    store, _ = bee_store
    assert len(store) == 2


def test_post_lecture_sting_only_sent_once_per_day(db_engine, bee_store):
    from recall.stings import send_post_lecture_sting

    _seed_due_question(db_engine)
    first = send_post_lecture_sting(now=_local(2026, 1, 1, 18, 10))
    second = send_post_lecture_sting(now=_local(2026, 1, 1, 18, 20))

    assert first is not None
    assert second is None


def test_sequential_ticks_send_start_then_middle_not_both_at_once(db_engine, bee_store):
    """Realistic continuous-polling usage: a tick at 18:00 sends "start";
    a later tick at 21:00 sends "middle" -- never double-fires in one call."""
    from recall.stings import maybe_send_stings

    _seed_due_question(db_engine)
    first = maybe_send_stings(now=_local(2026, 1, 1, 18, 0))
    second = maybe_send_stings(now=_local(2026, 1, 1, 21, 0))

    assert [e["slot"] for e in first] == ["start"]
    assert [e["slot"] for e in second] == ["middle"]
    store, _ = bee_store
    assert len(store) == 2


def test_cleanup_deletes_only_old_sting_todos(db_engine, bee_store):
    from recall.stings import cleanup_old_stings

    store, _ = bee_store
    store[10] = {"text": "Recall: old sting", "alarm_at": None, "deleted": False}
    store[11] = {"text": "Recall: recent sting", "alarm_at": None, "deleted": False}
    store[42] = {"text": "Buy milk", "alarm_at": None, "deleted": False}

    now = datetime.now(UTC)
    with DBSession(db_engine) as db:
        db.add(StingTodo(bee_todo_id=10, local_date="2026-01-01", slot="start", created_at=now - timedelta(hours=13)))
        db.add(StingTodo(bee_todo_id=11, local_date="2026-01-01", slot="middle", created_at=now - timedelta(hours=1)))
        db.commit()

    deleted = cleanup_old_stings(now=now)

    assert deleted == 1
    assert store[10]["deleted"] is True
    assert store[11]["deleted"] is False
    assert store[42]["deleted"] is False
