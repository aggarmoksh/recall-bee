import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine, select

from recall.db import ReadyTodo


class _FakeBeeClient:
    def __init__(self, store: dict, next_id_box: list):
        self.store = store
        self.next_id_box = next_id_box

    def create_todo(self, text, alarm_at=None):
        new_id = self.next_id_box[0]
        self.next_id_box[0] += 1
        self.store[new_id] = {"text": text, "completed": False, "deleted": False}
        return {"id": new_id}

    def get_todo(self, todo_id):
        if todo_id not in self.store or self.store[todo_id]["deleted"]:
            raise RuntimeError("404 not found")
        return {"id": todo_id, "completed": self.store[todo_id]["completed"]}


@pytest.fixture
def bee_store():
    return {}, [1]


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("recall.ready_todo.get_session", lambda: DBSession(engine))
    return engine


def test_ensure_creates_one_when_none_tracked(db_engine, bee_store):
    from recall.ready_todo import ensure_ready_todo

    store, next_id_box = bee_store
    client = _FakeBeeClient(store, next_id_box)

    todo_id = ensure_ready_todo(client)

    assert todo_id in store
    assert store[todo_id]["text"] == "Recall: tick when you can talk"
    with DBSession(db_engine) as db:
        assert len(db.exec(select(ReadyTodo)).all()) == 1


def test_ensure_returns_existing_without_creating_another(db_engine, bee_store):
    from recall.ready_todo import ensure_ready_todo

    store, next_id_box = bee_store
    client = _FakeBeeClient(store, next_id_box)

    first = ensure_ready_todo(client)
    second = ensure_ready_todo(client)

    assert first == second
    assert len(store) == 1


def test_check_and_recreate_noop_when_untouched(db_engine, bee_store):
    from recall.ready_todo import check_and_recreate, ensure_ready_todo

    store, next_id_box = bee_store
    client = _FakeBeeClient(store, next_id_box)
    ensure_ready_todo(client)

    result = check_and_recreate(client)

    assert result is None
    assert len(store) == 1


def test_check_and_recreate_detects_tick_and_recreates(db_engine, bee_store):
    from recall.ready_todo import check_and_recreate, ensure_ready_todo

    store, next_id_box = bee_store
    client = _FakeBeeClient(store, next_id_box)
    first_id = ensure_ready_todo(client)
    store[first_id]["completed"] = True

    result = check_and_recreate(client)

    assert result == {"event": "tick_detected"}
    assert len(store) == 2  # old one left as-is (ticked, not deleted by us), new one created
    with DBSession(db_engine) as db:
        rows = db.exec(select(ReadyTodo)).all()
        assert len(rows) == 1
        assert rows[0].bee_todo_id != first_id


def test_check_and_recreate_detects_deletion_and_recreates(db_engine, bee_store):
    from recall.ready_todo import check_and_recreate, ensure_ready_todo

    store, next_id_box = bee_store
    client = _FakeBeeClient(store, next_id_box)
    first_id = ensure_ready_todo(client)
    store[first_id]["deleted"] = True  # simulates the user deleting it on their phone

    result = check_and_recreate(client)

    assert result == {"event": "ready_todo_recreated", "reason": "missing"}
    with DBSession(db_engine) as db:
        rows = db.exec(select(ReadyTodo)).all()
        assert len(rows) == 1
        assert rows[0].bee_todo_id != first_id


def test_check_and_recreate_creates_one_if_none_tracked_yet(db_engine, bee_store):
    from recall.ready_todo import check_and_recreate

    store, next_id_box = bee_store
    client = _FakeBeeClient(store, next_id_box)

    result = check_and_recreate(client)

    assert result is None
    assert len(store) == 1
    with DBSession(db_engine) as db:
        assert len(db.exec(select(ReadyTodo)).all()) == 1
