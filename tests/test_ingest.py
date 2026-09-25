from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine, select

from recall.bee.client import BeeClient
from recall.db import Conversation, Session, Utterance
from recall.ingest import poll_once, rebuild_sessions

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


# Synthetic fixture: 2 conversations. Conversation 1 has two utterances 5 min
# apart (one session). Conversation 2 starts 20 min after conversation 1's
# last utterance (a session gap > 10 min -- should split into a new session).
FIXTURE_CONVERSATIONS = [
    {
        "id": 1001,
        "start_time": _ms(BASE_TIME),
        "end_time": None,
        "short_summary": "synthetic session one",
        "state": "CAPTURING",
        "updated_at": _ms(BASE_TIME),
        "conversation_uuid": "uuid-1001",
    },
    {
        "id": 1002,
        "start_time": _ms(BASE_TIME + timedelta(minutes=30)),
        "end_time": None,
        "short_summary": "synthetic session two",
        "state": "COMPLETED",
        "updated_at": _ms(BASE_TIME + timedelta(minutes=30)),
        "conversation_uuid": "uuid-1002",
    },
]

FIXTURE_TRANSCRIPTS = {
    1001: {
        "conversationId": 1001,
        "transcript": [
            {"id": 5001, "text": "synthetic utterance one", "spoken_at": _ms(BASE_TIME), "speaker": "Unknown"},
            {"id": 5002, "text": "synthetic utterance two", "spoken_at": _ms(BASE_TIME + timedelta(minutes=5))},
        ],
    },
    1002: {
        "conversationId": 1002,
        "transcript": [
            {"id": 5003, "text": "synthetic utterance three", "spoken_at": _ms(BASE_TIME + timedelta(minutes=30))},
        ],
    },
}


def _mock_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/conversations":
        return httpx.Response(200, json={"conversations": FIXTURE_CONVERSATIONS, "next_cursor": None})
    if path.startswith("/v1/conversations/"):
        conv_id = int(path.rsplit("/", 1)[-1])
        conv = next(c for c in FIXTURE_CONVERSATIONS if c["id"] == conv_id)
        return httpx.Response(200, json={"conversation": {**conv, "transcriptions": []}})
    return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def client_and_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("recall.ingest.get_session", lambda: DBSession(engine))

    client = BeeClient(base_url="http://127.0.0.1:8787")
    client._client = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(_mock_handler))

    # get_transcript() in recall/bee/client.py calls GET /v1/conversations/{id}
    # and reads `transcriptions[].utterances`, but our fixture doesn't model
    # that nesting -- patch get_transcript directly to serve the flattened
    # fixture shape instead, matching what the real (already-verified)
    # flattening logic produces.
    def fake_get_transcript(conversation_id, since=None):
        return FIXTURE_TRANSCRIPTS[conversation_id]

    monkeypatch.setattr(client, "get_transcript", fake_get_transcript)
    return client, engine


def test_poll_once_upserts_conversations_and_utterances(client_and_db):
    client, engine = client_and_db
    counts = poll_once(client)

    assert counts["conversations_touched"] == 2
    assert counts["utterances_added"] == 3

    with DBSession(engine) as db:
        convs = db.exec(select(Conversation)).all()
        utterances = db.exec(select(Utterance)).all()
        assert len(convs) == 2
        assert len(utterances) == 3
        assert {u.id for u in utterances} == {5001, 5002, 5003}


def test_poll_once_is_idempotent_on_rerun(client_and_db):
    client, engine = client_and_db
    poll_once(client)
    counts_second = poll_once(client)

    # updated_at unchanged -> already_current -> nothing re-touched
    assert counts_second["conversations_touched"] == 0
    assert counts_second["utterances_added"] == 0

    with DBSession(engine) as db:
        assert len(db.exec(select(Utterance)).all()) == 3


def test_poll_once_builds_sessions_from_spoken_at_gaps(client_and_db):
    client, engine = client_and_db
    poll_once(client)

    with DBSession(engine) as db:
        sessions = db.exec(select(Session).order_by(Session.start_at)).all()
        # utterances 1+2 are 5 min apart (same session); utterance 3 is
        # 25 min after utterance 2 (> 10 min gap -> new session)
        assert len(sessions) == 2
        assert sessions[0].conversation_ids == [1001]
        assert sessions[1].conversation_ids == [1002]


def test_rebuild_sessions_never_gates_on_conversation_state(client_and_db):
    """Conversation 1001 is 'CAPTURING' and 1002 is 'COMPLETED' in the
    fixture -- both must still produce ingested utterances and sessions."""
    client, engine = client_and_db
    poll_once(client)

    with DBSession(engine) as db:
        conv = db.get(Conversation, 1001)
        assert conv.state == "CAPTURING"
        utterances = db.exec(select(Utterance).where(Utterance.conversation_id == 1001)).all()
        assert len(utterances) == 2


def test_rebuild_sessions_directly_with_synthetic_utterances(client_and_db):
    _, engine = client_and_db
    with DBSession(engine) as db:
        db.add(Conversation(id=1))
        db.add_all(
            [
                Utterance(id=1, conversation_id=1, conversation_uuid="x", text="a", spoken_at=BASE_TIME),
                Utterance(id=2, conversation_id=1, conversation_uuid="x", text="b", spoken_at=BASE_TIME + timedelta(minutes=3)),
                Utterance(id=3, conversation_id=1, conversation_uuid="x", text="c", spoken_at=BASE_TIME + timedelta(minutes=20)),
            ]
        )
        db.commit()
        count = rebuild_sessions(db)
        assert count == 2
