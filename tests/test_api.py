from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from recall.api import _window_status, app
from recall.db import Answer, Conversation, Dispatch, Question
from recall.db import Session as SessionModel

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("recall.api._readonly_engine", engine)
    monkeypatch.setattr("recall.questions.get_session", lambda: DBSession(engine))
    return engine


@pytest.fixture
def client(db_engine):
    return TestClient(app)


def _seed_basic(engine, *, approval_status="approved", session_type="lecture_study"):
    with DBSession(engine) as db:
        db.add(Conversation(id=1))
        session = SessionModel(
            start_at=BASE,
            end_at=BASE + timedelta(minutes=12),
            conversation_ids=[1],
            session_type=session_type,
            session_type_reason="a structured lecture",
            approval_status=approval_status,
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        q = Question(
            conversation_id=1,
            session_id=session.id,
            text="Why did the sky turn red?",
            expected_answer="Rayleigh scattering at sunset.",
            rubric=["mentions scattering", "mentions wavelength", "mentions sunset angle"],
            source_excerpt="the sky turns red at sunset because of scattering",
            topic="Optics",
            active=True,
        )
        db.add(q)
        db.commit()
        db.refresh(q)
        return session.id, q.id


def _seed_dispatch_and_answer(engine, question_id, *, alarm_at, verdict="correct", quality=5, rubric_points_hit=None, matched_at=None, bee_todo_id=1):
    with DBSession(engine) as db:
        d = Dispatch(question_id=question_id, bee_todo_id=bee_todo_id, alarm_at=alarm_at, status="pending", dispatched_at=alarm_at)
        db.add(d)
        db.commit()
        db.refresh(d)
        a = Answer(
            dispatch_id=d.id,
            raw_text="It turns red because light scatters more at low angles.",
            verdict=verdict,
            quality=quality,
            rubric_points_hit=rubric_points_hit or [],
            feedback="Missed the wavelength detail.",
            matched_at=matched_at or (alarm_at + timedelta(seconds=30)),
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        return d.id, a.id


# --- /api/today ---


def test_today_reports_profile_and_dispatch_count(client, db_engine):
    # dispatched_at must be "now", not an offset -- dispatches_today() uses
    # LOCAL day boundaries (Phase 4B fix), so "now minus 1h" can land in
    # yesterday's local day and make this test flaky right after local
    # midnight. "now" itself is always in today's local day by definition.
    session_id, question_id = _seed_basic(db_engine)
    _seed_dispatch_and_answer(db_engine, question_id, alarm_at=datetime.now(UTC))

    resp = client.get("/api/today")
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"] in ("normal", "dense")
    assert body["dispatches_today"] == 1
    assert body["open_dispatch"] is None
    assert len(body["recent_answers"]) == 1
    assert body["recent_answers"][0]["verdict"] == "correct"


def test_today_shows_open_dispatch_with_window_status(client, db_engine):
    session_id, question_id = _seed_basic(db_engine)
    now = datetime.now(UTC)
    with DBSession(db_engine) as db:
        d = Dispatch(question_id=question_id, bee_todo_id=99, alarm_at=now + timedelta(minutes=5), status="pending", dispatched_at=now)
        db.add(d)
        db.commit()

    resp = client.get("/api/today")
    body = resp.json()
    assert body["open_dispatch"] is not None
    assert body["open_dispatch"]["question_id"] == question_id
    assert body["open_dispatch"]["window_status"] == "waiting"


def test_window_status_transitions():
    alarm_at = BASE
    assert _window_status(alarm_at, alarm_at - timedelta(seconds=10)) == "waiting"
    assert _window_status(alarm_at, alarm_at + timedelta(seconds=10)) == "listening"
    assert _window_status(alarm_at, alarm_at + timedelta(seconds=301)) == "grading"


# --- /api/answers/{id} ---


def test_answer_detail_includes_rubric_hit_miss_and_source(client, db_engine):
    session_id, question_id = _seed_basic(db_engine)
    _, answer_id = _seed_dispatch_and_answer(
        db_engine,
        question_id,
        alarm_at=BASE,
        verdict="partial",
        quality=3,
        rubric_points_hit=["mentions scattering of light"],  # close paraphrase, not exact
    )

    resp = client.get(f"/api/answers/{answer_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "partial"
    assert body["quality"] == 3
    assert body["answer_transcript"].startswith("It turns red")
    assert body["source_excerpt"] == "the sky turns red at sunset because of scattering"
    assert body["session_datetime"] is not None

    rubric = {r["point"]: r["hit"] for r in body["rubric"]}
    assert rubric["mentions scattering"] is True  # fuzzy-matched
    assert rubric["mentions wavelength"] is False
    assert rubric["mentions sunset angle"] is False


def test_answer_detail_feedback_text_matches_wrist_format(client, db_engine):
    session_id, question_id = _seed_basic(db_engine)
    _, answer_id = _seed_dispatch_and_answer(
        db_engine, question_id, alarm_at=BASE, verdict="partial", quality=3, rubric_points_hit=["mentions scattering"]
    )

    resp = client.get(f"/api/answers/{answer_id}")
    body = resp.json()
    assert body["feedback_text"] == "Recall ✓ 1/3: Missed the wavelength detail."


def test_answer_detail_404_for_missing_id(client, db_engine):
    resp = client.get("/api/answers/999")
    assert resp.status_code == 404


# --- /api/mastery ---


def test_mastery_reports_per_topic_counts_and_quality(client, db_engine):
    session_id, question_id = _seed_basic(db_engine)
    _seed_dispatch_and_answer(db_engine, question_id, alarm_at=BASE, verdict="incorrect", quality=1, bee_todo_id=1)
    _seed_dispatch_and_answer(db_engine, question_id, alarm_at=BASE + timedelta(days=1), verdict="correct", quality=5, bee_todo_id=2)

    resp = client.get("/api/mastery")
    body = resp.json()
    assert len(body["topics"]) == 1
    topic = body["topics"][0]
    assert topic["topic"] == "Optics"
    assert topic["question_count"] == 1
    assert topic["average_latest_quality"] == 5  # latest answer, not the average of all answers

    assert len(body["retention_series"]) == 2  # two distinct days


def test_mastery_excludes_code_truncated_answers(client, db_engine):
    session_id, question_id = _seed_basic(db_engine)
    _seed_dispatch_and_answer(db_engine, question_id, alarm_at=BASE, verdict="code_truncated", quality=0, bee_todo_id=1)

    resp = client.get("/api/mastery")
    body = resp.json()
    assert body["topics"][0]["average_latest_quality"] is None  # the only answer is excluded


# --- /api/sessions ---


def test_sessions_lists_status_and_question_count(client, db_engine):
    session_id, question_id = _seed_basic(db_engine, approval_status="approved")

    resp = client.get("/api/sessions")
    body = resp.json()
    assert len(body["sessions"]) == 1
    s = body["sessions"][0]
    assert s["session_id"] == session_id
    assert s["status"] == "approved"
    assert s["session_type"] == "lecture_study"
    assert s["question_count"] == 1
    assert s["duration_minutes"] == 12.0
    assert "transcript" not in s


def test_sessions_reports_excluded_status(client, db_engine, monkeypatch):
    session_id, _ = _seed_basic(db_engine)
    monkeypatch.setattr("recall.api.load_excluded_sessions", lambda: {session_id})

    resp = client.get("/api/sessions")
    body = resp.json()
    assert body["sessions"][0]["status"] == "excluded"


# --- /api/privacy ---


def test_privacy_reports_skip_and_rejection_counts(client, db_engine):
    session_id, question_id = _seed_basic(db_engine, session_type="lecture_study")
    with DBSession(db_engine) as db:
        db.add(SessionModel(start_at=BASE + timedelta(hours=1), end_at=BASE + timedelta(hours=2), conversation_ids=[1], session_type="casual", approval_status="none"))
        db.add(SessionModel(start_at=BASE + timedelta(hours=3), end_at=BASE + timedelta(hours=4), conversation_ids=[1], session_type="personal", approval_status="none"))
        db.add(Question(conversation_id=1, text="rejected one", expected_answer="a", source_excerpt="...", active=False))
        db.commit()

    resp = client.get("/api/privacy")
    body = resp.json()
    assert body["sessions_skipped_by_type"] == {"casual": 1, "personal": 1}
    assert body["questions_rejected_count"] == 1
    assert len(body["rules"]) > 0


# --- write routes ---


def test_approve_session_route(client, db_engine):
    session_id, _ = _seed_basic(db_engine, approval_status="pending")
    resp = client.post(f"/api/sessions/{session_id}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    with DBSession(db_engine) as db:
        assert db.get(SessionModel, session_id).approval_status == "approved"


def test_reject_session_route(client, db_engine):
    session_id, _ = _seed_basic(db_engine, approval_status="pending")
    resp = client.post(f"/api/sessions/{session_id}/reject")
    assert resp.status_code == 200
    with DBSession(db_engine) as db:
        assert db.get(SessionModel, session_id).approval_status == "rejected"


def test_approve_session_rejects_non_pending(client, db_engine):
    session_id, _ = _seed_basic(db_engine, approval_status="approved")
    resp = client.post(f"/api/sessions/{session_id}/approve")
    assert resp.status_code == 400


def test_reject_question_route(client, db_engine):
    session_id, question_id = _seed_basic(db_engine)
    resp = client.post(f"/api/questions/{question_id}/reject")
    assert resp.status_code == 200
    with DBSession(db_engine) as db:
        assert db.get(Question, question_id).active is False


def test_write_routes_never_touch_readonly_connection_mismatch(client, db_engine):
    """Sanity check that a write followed by a read on the SAME fixture db
    is visible -- proves the write path and the read-only-engine read path
    are pointed at the same underlying file in this test setup."""
    session_id, _ = _seed_basic(db_engine, approval_status="pending")
    client.post(f"/api/sessions/{session_id}/approve")
    resp = client.get("/api/sessions")
    assert resp.json()["sessions"][0]["status"] == "approved"
