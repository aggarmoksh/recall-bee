from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from recall.api import app
from recall.db import Answer, Conversation, Dispatch, Question, SessionRun
from recall.db import Session as SessionModel

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("recall.api._readonly_engine", engine)
    monkeypatch.setattr("recall.questions.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.scoring.get_session", lambda: DBSession(engine))
    return engine


@pytest.fixture
def client(db_engine):
    return TestClient(app)


def _seed_basic(engine, *, approval_status="approved", session_type="lecture_study", topic="Optics"):
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
            topic=topic,
            active=True,
        )
        db.add(q)
        db.commit()
        db.refresh(q)
        return session.id, q.id


def _seed_dispatch_and_answer(engine, question_id, *, alarm_at, verdict="correct", quality=5, rubric_points_hit=None, matched_at=None, bee_todo_id=1, session_run_id=None):
    with DBSession(engine) as db:
        d = Dispatch(question_id=question_id, bee_todo_id=bee_todo_id, alarm_at=alarm_at, status="pending", dispatched_at=alarm_at, session_run_id=session_run_id)
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


def test_today_idle_with_no_active_session(client, db_engine):
    session_id, question_id = _seed_basic(db_engine)
    _seed_dispatch_and_answer(db_engine, question_id, alarm_at=datetime.now(UTC))

    resp = client.get("/api/today")
    assert resp.status_code == 200
    body = resp.json()
    assert body["daily_quota"] == 6
    assert body["session"]["state"] == "idle"
    assert len(body["recent_answers"]) == 1
    assert body["recent_answers"][0]["verdict"] == "correct"


def test_today_shows_in_session_state_with_current_question(client, db_engine):
    session_id, question_id = _seed_basic(db_engine)
    now = datetime.now(UTC)
    with DBSession(db_engine) as db:
        run = SessionRun(trigger="tick", status="active", started_at=now, questions_asked=1)
        db.add(run)
        db.commit()
        db.refresh(run)
        d = Dispatch(question_id=question_id, bee_todo_id=99, alarm_at=now + timedelta(minutes=5), status="pending", dispatched_at=now, session_run_id=run.id)
        db.add(d)
        db.commit()

    resp = client.get("/api/today")
    body = resp.json()
    assert body["session"]["state"] == "in_session"
    assert body["session"]["questions_asked"] == 1
    assert body["session"]["current_question"]["question_id"] == question_id


def test_today_current_question_skips_a_deferred_dispatch(client, db_engine):
    """A deferred (status='processing') dispatch has no Answer yet either,
    same as the real open one -- must not be shown as the current question
    (regression: _active_session_payload used to match on 'no Answer'
    alone, which an older deferred dispatch also satisfies)."""
    session_id, question_id = _seed_basic(db_engine)
    now = datetime.now(UTC)
    with DBSession(db_engine) as db:
        q2 = Question(conversation_id=1, session_id=session_id, text="Q2", expected_answer="A", rubric=["a"], source_excerpt="...")
        db.add(q2)
        db.commit()
        db.refresh(q2)
        q2_id = q2.id

        run = SessionRun(trigger="tick", status="active", started_at=now - timedelta(minutes=3), questions_asked=2)
        db.add(run)
        db.commit()
        db.refresh(run)
        db.add(Dispatch(question_id=question_id, bee_todo_id=50, alarm_at=now - timedelta(minutes=3), status="processing", dispatched_at=now - timedelta(minutes=3), session_run_id=run.id))
        db.add(Dispatch(question_id=q2_id, bee_todo_id=51, alarm_at=now, status="pending", dispatched_at=now, session_run_id=run.id))
        db.commit()

    resp = client.get("/api/today")
    body = resp.json()
    assert body["session"]["current_question"]["question_id"] == q2_id


def test_today_includes_report_card_for_a_session_ended_today(client, db_engine):
    session_id, question_id = _seed_basic(db_engine)
    now = datetime.now(UTC)
    with DBSession(db_engine) as db:
        db.add(SessionRun(trigger="tick", status="completed", started_at=now - timedelta(minutes=5), ended_at=now, questions_asked=3, questions_answered=3))
        db.commit()

    resp = client.get("/api/today")
    body = resp.json()
    assert body["report_card"] is not None
    assert body["report_card"]["questions_asked"] == 3
    assert body["report_card"]["questions_answered"] == 3


def test_today_report_card_counts_still_processing_dispatches(client, db_engine):
    """Phase 5E Step 3: a deferred (not-yet-graded) dispatch counts toward
    'still processing' on the dashboard, separate from questions_answered."""
    session_id, question_id = _seed_basic(db_engine)
    now = datetime.now(UTC)
    with DBSession(db_engine) as db:
        run = SessionRun(trigger="tick", status="paused", started_at=now - timedelta(minutes=5), ended_at=now, questions_asked=2, questions_answered=0)
        db.add(run)
        db.commit()
        db.refresh(run)
        db.add(Dispatch(question_id=question_id, bee_todo_id=77, alarm_at=now - timedelta(minutes=3), status="processing", dispatched_at=now - timedelta(minutes=3), session_run_id=run.id))
        db.commit()

    resp = client.get("/api/today")
    body = resp.json()
    assert body["report_card"] is not None
    assert body["report_card"]["questions_processing"] == 1


class _FakeExternalAccessClient:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def _request(self, method, path, **kwargs):
        return self._response


class _FakeExternalAccessProxy:
    def __init__(self, port=None):
        self.base_url = "http://fake"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_external_access_proxies_bee_response(client, monkeypatch):
    fake_response = {"external_access": [{"id": 1, "name": "Some Service"}], "next_cursor": None}
    monkeypatch.setattr("recall.api.BeeProxy", lambda port=None: _FakeExternalAccessProxy())
    monkeypatch.setattr("recall.api.BeeClient", lambda base_url: _FakeExternalAccessClient(fake_response))

    resp = client.get("/api/privacy/external-access")
    assert resp.status_code == 200
    assert resp.json() == fake_response


def test_external_access_returns_502_on_bee_failure(client, monkeypatch):
    def _raise(port=None):
        raise RuntimeError("bee proxy did not become ready")

    monkeypatch.setattr("recall.api.BeeProxy", _raise)

    resp = client.get("/api/privacy/external-access")
    assert resp.status_code == 502


def test_today_no_report_card_when_nothing_ended_yet(client, db_engine):
    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        db.commit()

    resp = client.get("/api/today")
    assert resp.json()["report_card"] is None


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


def test_answer_detail_includes_raw_feedback_and_hint_flag(client, db_engine):
    session_id, question_id = _seed_basic(db_engine)
    _, answer_id = _seed_dispatch_and_answer(
        db_engine, question_id, alarm_at=BASE, verdict="partial", quality=3, rubric_points_hit=["mentions scattering"]
    )
    with DBSession(db_engine) as db:
        a = db.get(Answer, answer_id)
        a.hint_in_notification = True
        db.add(a)
        db.commit()

    resp = client.get(f"/api/answers/{answer_id}")
    body = resp.json()
    assert body["feedback"] == "Missed the wavelength detail."
    assert body["hint_in_notification"] is True


def test_answer_detail_404_for_missing_id(client, db_engine):
    resp = client.get("/api/answers/999")
    assert resp.status_code == 404


# --- /api/mastery (Phase 5C fix: grouped by source session) ---


def test_mastery_groups_by_session_with_questions_underneath(client, db_engine):
    session_id, question_id = _seed_basic(db_engine, topic="Optics")
    _seed_dispatch_and_answer(db_engine, question_id, alarm_at=BASE, verdict="incorrect", quality=1, bee_todo_id=1)
    _seed_dispatch_and_answer(db_engine, question_id, alarm_at=BASE + timedelta(days=1), verdict="correct", quality=5, bee_todo_id=2)

    resp = client.get("/api/mastery")
    body = resp.json()
    assert len(body["sessions"]) == 1
    session_row = body["sessions"][0]
    assert session_row["session_id"] == session_id
    assert session_row["label"] == "Optics"
    assert session_row["question_count"] == 1
    assert session_row["average_quality"] == 5  # latest answer, not the average of all answers
    assert len(session_row["questions"]) == 1
    assert session_row["questions"][0]["question_id"] == question_id
    assert session_row["questions"][0]["latest_quality"] == 5

    assert len(body["retention_series"]) == 2  # two distinct days


def test_mastery_excludes_code_truncated_answers(client, db_engine):
    session_id, question_id = _seed_basic(db_engine)
    _seed_dispatch_and_answer(db_engine, question_id, alarm_at=BASE, verdict="code_truncated", quality=0, bee_todo_id=1)

    resp = client.get("/api/mastery")
    body = resp.json()
    assert body["sessions"][0]["average_quality"] is None  # the only answer is excluded


def test_mastery_labels_no_answer_question_as_missed_not_counted(client, db_engine):
    session_id, question_id = _seed_basic(db_engine)
    _seed_dispatch_and_answer(db_engine, question_id, alarm_at=BASE, verdict="no_answer", quality=0, bee_todo_id=1)

    resp = client.get("/api/mastery")
    body = resp.json()
    assert body["sessions"][0]["questions"][0]["status_label"] == "Missed, not counted against you"


def test_mastery_labels_unclassified_session_as_capturing(client, db_engine):
    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        session = SessionModel(start_at=BASE, end_at=BASE, conversation_ids=[1])  # session_type still None
        db.add(session)
        db.commit()
        db.refresh(session)
        q = Question(conversation_id=1, session_id=session.id, text="Q", expected_answer="A", rubric=["a"], source_excerpt="...", topic="X")
        db.add(q)
        db.commit()

    resp = client.get("/api/mastery")
    body = resp.json()
    assert body["sessions"][0]["questions"][0]["status_label"] == "Capturing..."


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


def test_sessions_reports_skipped_for_casual_and_personal(client, db_engine):
    session_id, _ = _seed_basic(db_engine, session_type="casual", approval_status="none")

    resp = client.get("/api/sessions")
    body = resp.json()
    assert body["sessions"][0]["status"] == "Skipped"


def test_sessions_reports_capturing_for_unclassified(client, db_engine):
    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        db.add(SessionModel(start_at=BASE, end_at=BASE, conversation_ids=[1]))
        db.commit()

    resp = client.get("/api/sessions")
    body = resp.json()
    assert body["sessions"][0]["status"] == "Capturing..."


# --- /api/privacy (Phase 5C fix: only privacy exclusions) ---


def test_privacy_reports_skip_counts_only_not_question_rejections(client, db_engine):
    session_id, question_id = _seed_basic(db_engine, session_type="lecture_study")
    with DBSession(db_engine) as db:
        db.add(SessionModel(start_at=BASE + timedelta(hours=1), end_at=BASE + timedelta(hours=2), conversation_ids=[1], session_type="casual", approval_status="none"))
        db.add(SessionModel(start_at=BASE + timedelta(hours=3), end_at=BASE + timedelta(hours=4), conversation_ids=[1], session_type="personal", approval_status="none"))
        db.add(Question(conversation_id=1, text="rejected one", expected_answer="a", source_excerpt="...", active=False))
        db.commit()

    resp = client.get("/api/privacy")
    body = resp.json()
    assert body["sessions_skipped_by_type"] == {"casual": 1, "personal": 1}
    assert "questions_rejected_count" not in body
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
