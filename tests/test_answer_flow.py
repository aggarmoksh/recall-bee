from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine, select

from recall.answer_flow import cleanup_old_feedback_todos, process_dispatch_answer
from recall.db import Answer, Conversation, Dispatch, FeedbackTodo, Question, ReviewState, Utterance
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
    """Records every create/delete call against a shared in-memory todo
    store, so tests can assert exactly which ids were touched."""

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
    return {}, [1000]  # (store, next_id_box)


@pytest.fixture
def db_engine(tmp_path, monkeypatch, bee_store):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("recall.answer_flow.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.grading.get_session", lambda: DBSession(engine))

    store, next_id_box = bee_store
    monkeypatch.setattr("recall.answer_flow.BeeProxy", lambda port=None: _FakeBeeProxy())
    monkeypatch.setattr("recall.answer_flow.BeeClient", lambda base_url: _FakeBeeClient(store, next_id_box))
    return engine


def _seed(engine, grade_result: GradeResult, answer_text="a reasonably long spoken answer with plenty of extra words included here to be safe", user_todo_id=None):
    with DBSession(engine) as db:
        db.add(Conversation(id=1))
        q = Question(conversation_id=1, text="Why?", expected_answer="Because.", rubric=["a", "b"], source_excerpt="...")
        db.add(q)
        db.commit()
        db.refresh(q)

        dispatch = Dispatch(question_id=q.id, bee_todo_id=1, alarm_at=BASE, status="pending")
        db.add(dispatch)
        db.commit()
        db.refresh(dispatch)

        db.add(Utterance(id=1, conversation_id=1, conversation_uuid="x", text=answer_text, spoken_at=BASE + timedelta(seconds=5)))
        db.commit()

        dispatch_id = dispatch.id
        question_id = q.id
    return dispatch_id, question_id


@pytest.fixture(autouse=True)
def patch_provider(monkeypatch):
    def _patch(result: GradeResult):
        monkeypatch.setattr("recall.grading.get_provider", lambda: _FakeProvider(result))

    return _patch


def test_correct_answer_deletes_question_todo_and_posts_checkmark_feedback(db_engine, bee_store, patch_provider):
    store, _ = bee_store
    store[1] = {"text": "Recall: Why?", "alarm_at": None, "deleted": False}  # the question todo, pre-existing
    patch_provider(GradeResult(verdict="correct", rubric_points_hit=["a", "b"], quality=5, feedback="Nailed it."))

    dispatch_id, question_id = _seed(db_engine, GradeResult(verdict="correct", rubric_points_hit=["a", "b"], quality=5, feedback="Nailed it."))
    answer = process_dispatch_answer(dispatch_id)

    assert answer.verdict == "correct"
    assert store[1]["deleted"] is True  # question todo deleted

    feedback_ids = [k for k, v in store.items() if k != 1]
    assert len(feedback_ids) == 1
    assert "✓ 2/2: Nailed it." in store[feedback_ids[0]]["text"]

    with DBSession(db_engine) as db:
        dispatch = db.get(Dispatch, dispatch_id)
        assert dispatch.status == "answered"
        assert len(db.exec(select(FeedbackTodo)).all()) == 1
        review_state = db.exec(select(ReviewState).where(ReviewState.question_id == question_id)).first()
        assert review_state.repetitions == 1  # SM-2 updated


def test_incorrect_answer_posts_x_feedback(db_engine, bee_store, patch_provider):
    store, _ = bee_store
    store[1] = {"text": "Recall: Why?", "alarm_at": None, "deleted": False}
    patch_provider(GradeResult(verdict="incorrect", rubric_points_hit=[], quality=1, feedback="Wrong direction entirely."))

    dispatch_id, _ = _seed(db_engine, GradeResult(verdict="incorrect", rubric_points_hit=[], quality=1, feedback="Wrong direction entirely."))
    process_dispatch_answer(dispatch_id)

    feedback_ids = [k for k, v in store.items() if k != 1]
    assert "✗: Wrong direction entirely." in store[feedback_ids[0]]["text"]


def test_incomplete_capture_does_not_update_sm2_and_redispatches_in_2h(db_engine, bee_store, patch_provider):
    store, _ = bee_store
    store[1] = {"text": "Recall: Why?", "alarm_at": None, "deleted": False}
    patch_provider(GradeResult(verdict="partial", rubric_points_hit=["a"], quality=3, feedback="Missed b"))

    dispatch_id, question_id = _seed(db_engine, GradeResult(verdict="partial", rubric_points_hit=["a"], quality=3, feedback="Missed b"), answer_text="only four words")

    with DBSession(db_engine) as db:
        db.add(ReviewState(question_id=question_id, ease_factor=2.5, interval_days=6, repetitions=2, due_at=BASE))
        db.commit()

    answer = process_dispatch_answer(dispatch_id)

    assert answer.verdict == "incomplete_capture"
    feedback_ids = [k for k, v in store.items() if k != 1]
    assert "Only caught a few words, will ask again" in store[feedback_ids[0]]["text"]

    with DBSession(db_engine) as db:
        review_state = db.exec(select(ReviewState).where(ReviewState.question_id == question_id)).first()
        # untouched by SM-2
        assert review_state.ease_factor == 2.5
        assert review_state.interval_days == 6
        assert review_state.repetitions == 2
        # but re-scheduled to ~2h from now
        assert review_state.due_at < datetime.now(UTC) + timedelta(hours=2, minutes=1)
        assert review_state.due_at > datetime.now(UTC) + timedelta(hours=1, minutes=55)


def test_user_todos_are_never_touched(db_engine, bee_store, patch_provider):
    """The hard guard: a user's own todo sitting in the same account must
    never be deleted, even though the fake client would technically allow
    deleting any id it's told to."""
    store, _ = bee_store
    store[1] = {"text": "Recall: Why?", "alarm_at": None, "deleted": False}  # our question todo
    store[42] = {"text": "Buy milk", "alarm_at": None, "deleted": False}  # user's own unrelated todo
    store[99] = {"text": "Call the dentist", "alarm_at": None, "deleted": False}  # another user todo

    patch_provider(GradeResult(verdict="correct", rubric_points_hit=["a", "b"], quality=5, feedback="Great."))
    dispatch_id, _ = _seed(db_engine, GradeResult(verdict="correct", rubric_points_hit=["a", "b"], quality=5, feedback="Great."))
    process_dispatch_answer(dispatch_id)

    assert store[42]["deleted"] is False
    assert store[99]["deleted"] is False
    assert store[1]["deleted"] is True  # only the tracked question todo


def test_cleanup_deletes_only_old_tracked_feedback_todos(db_engine, bee_store):
    store, _ = bee_store
    store[10] = {"text": "Recall ✓ 2/2: old feedback", "alarm_at": None, "deleted": False}
    store[11] = {"text": "Recall ✓ 2/2: recent feedback", "alarm_at": None, "deleted": False}
    store[42] = {"text": "Buy milk", "alarm_at": None, "deleted": False}  # untracked user todo

    now = datetime.now(UTC)
    with DBSession(db_engine) as db:
        db.add(FeedbackTodo(bee_todo_id=10, created_at=now - timedelta(hours=13)))  # old -> delete
        db.add(FeedbackTodo(bee_todo_id=11, created_at=now - timedelta(hours=1)))  # recent -> keep
        db.commit()

    deleted_count = cleanup_old_feedback_todos(now=now)

    assert deleted_count == 1
    assert store[10]["deleted"] is True
    assert store[11]["deleted"] is False
    assert store[42]["deleted"] is False  # never touched -- not in FeedbackTodo at all

    with DBSession(db_engine) as db:
        remaining = db.exec(select(FeedbackTodo)).all()
        assert len(remaining) == 1
        assert remaining[0].bee_todo_id == 11


def test_cleanup_noop_when_nothing_old(db_engine, bee_store):
    deleted_count = cleanup_old_feedback_todos()
    assert deleted_count == 0
