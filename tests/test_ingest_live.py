"""Phase 5E Step 1: fuzzy dedupe, the 5s live poller, and immediate-fetch
on a processed update-conversation stream event."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine, select

from recall.db import Conversation, Utterance
from recall.ingest import (
    _find_fuzzy_duplicate,
    _ingest_conversation_once,
    poll_live_conversations,
    upsert_utterance,
)

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("recall.ingest.get_session", lambda: DBSession(engine))
    return engine


def test_fuzzy_duplicate_matches_same_text_within_2s(engine):
    with DBSession(engine) as db:
        db.add(Conversation(id=1))
        db.add(Utterance(id=100, conversation_id=1, conversation_uuid="x", text="Hello there friend", spoken_at=BASE_TIME))
        db.commit()
        match = _find_fuzzy_duplicate(db, 1, "hello there, friend!", BASE_TIME + timedelta(seconds=1.5))
        assert match is not None
        assert match.id == 100


def test_fuzzy_duplicate_rejects_outside_2s_window(engine):
    with DBSession(engine) as db:
        db.add(Conversation(id=1))
        db.add(Utterance(id=100, conversation_id=1, conversation_uuid="x", text="Hello there friend", spoken_at=BASE_TIME))
        db.commit()
        match = _find_fuzzy_duplicate(db, 1, "hello there friend", BASE_TIME + timedelta(seconds=3))
        assert match is None


def test_fuzzy_duplicate_rejects_different_text(engine):
    with DBSession(engine) as db:
        db.add(Conversation(id=1))
        db.add(Utterance(id=100, conversation_id=1, conversation_uuid="x", text="Hello there friend", spoken_at=BASE_TIME))
        db.commit()
        match = _find_fuzzy_duplicate(db, 1, "goodbye now", BASE_TIME)
        assert match is None


def test_upsert_utterance_skips_fuzzy_duplicate_from_realtime_then_final(engine):
    """Realtime utterance id=100 arrives first; the final transcript later
    carries the same speech under id=200. Must not create a second row,
    and must upgrade the existing row's text (evidence) without touching
    its id."""
    with DBSession(engine) as db:
        db.add(Conversation(id=1))
        db.commit()
        inserted = upsert_utterance(db, 1, "x", {"id": 100, "text": "realtime version", "spoken_at": BASE_TIME.isoformat()})
        db.commit()
        assert inserted is True

        inserted_again = upsert_utterance(
            db, 1, "x", {"id": 200, "text": "realtime version", "spoken_at": (BASE_TIME + timedelta(seconds=1)).isoformat()}, is_final=True
        )
        db.commit()
        assert inserted_again is False

        rows = db.exec(select(Utterance)).all()
        assert len(rows) == 1
        assert rows[0].id == 100
        assert rows[0].text == "realtime version"  # upgraded to the final, more accurate text


def test_upsert_utterance_does_not_upgrade_text_from_non_final_duplicate(engine):
    """A fuzzy duplicate arriving via the realtime path (is_final=False)
    must not overwrite existing text -- only the final pass is trusted to
    upgrade evidence."""
    with DBSession(engine) as db:
        db.add(Conversation(id=1))
        db.commit()
        upsert_utterance(db, 1, "x", {"id": 100, "text": "original text", "spoken_at": BASE_TIME.isoformat()})
        db.commit()
        upsert_utterance(
            db, 1, "x", {"id": 200, "text": "original text", "spoken_at": (BASE_TIME + timedelta(seconds=1)).isoformat()}, is_final=False
        )
        db.commit()
        rows = db.exec(select(Utterance)).all()
        assert len(rows) == 1
        assert rows[0].text == "original text"


class _FakeClient:
    def __init__(self, conversations: list[dict], transcripts: dict[int, dict]):
        self._conversations = conversations
        self._transcripts = transcripts

    def list_conversations(self, limit=10, cursor=None):
        return {"conversations": self._conversations, "next_cursor": None}

    def get_transcript(self, conv_id, since=None):
        return self._transcripts[conv_id]


def test_ingest_conversation_once_stores_state_and_utterances(engine):
    client = _FakeClient(
        [],
        {5: {"conversationId": 5, "transcript": [{"id": 1, "text": "hi", "spoken_at": BASE_TIME.isoformat()}], "state": "CAPTURING"}},
    )
    with DBSession(engine) as db:
        added = _ingest_conversation_once(db, client, 5)
        assert added == 1
        conv = db.get(Conversation, 5)
        assert conv.state == "CAPTURING"
        assert db.exec(select(Utterance)).all()[0].text == "hi"


def test_ingest_conversation_once_fires_callback_only_for_new_utterances(engine):
    """Phase 5E Step 2: spoken-start detection runs on utterances from
    both the poller and the stream, deduped so one phrase starts one
    session -- simulated here as the stream having already inserted an
    utterance before the poller sees the same conversation."""
    client = _FakeClient(
        [],
        {
            5: {
                "conversationId": 5,
                "transcript": [
                    {"id": 1, "text": "already seen via stream", "spoken_at": BASE_TIME.isoformat()},
                    {"id": 2, "text": "genuinely new", "spoken_at": (BASE_TIME + timedelta(seconds=5)).isoformat()},
                ],
                "state": "CAPTURING",
            }
        },
    )
    calls = []
    with DBSession(engine) as db:
        db.add(Conversation(id=5))
        db.add(Utterance(id=1, conversation_id=5, conversation_uuid="x", text="already seen via stream", spoken_at=BASE_TIME))
        db.commit()
        _ingest_conversation_once(db, client, 5, on_new_utterance=lambda uid, text, spoken_at: calls.append(uid))

    assert calls == [2]  # only the genuinely new one fires the callback


def test_poll_live_conversations_only_touches_non_final_states(engine):
    client = _FakeClient(
        [
            {"id": 1, "state": "CAPTURING"},
            {"id": 2, "state": "COMPLETED"},  # must be skipped by the live poller
        ],
        {1: {"conversationId": 1, "transcript": [{"id": 10, "text": "live speech", "spoken_at": BASE_TIME.isoformat()}], "state": "CAPTURING"}},
    )

    async def run_one_tick():
        task = asyncio.ensure_future(poll_live_conversations(client))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_one_tick())

    with DBSession(engine) as db:
        assert db.get(Conversation, 1) is not None
        assert db.get(Conversation, 2) is None  # skipped -- already COMPLETED
        utterances = db.exec(select(Utterance)).all()
        assert len(utterances) == 1
        assert utterances[0].id == 10
