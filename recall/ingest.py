"""Ingestion: polls conversations on an interval, upserts Conversation/
Utterance rows, refreshes Session boundaries, and (in long-running mode)
consumes the live stream to upsert new-utterance events as they arrive.

`/v1/changes` was checked as a possible polling cursor and rejected: the
raw REST response is bare ID lists with no cursor field at all -- the
richer since/until/next_cursor wrapper `bee changed --json` shows is
CLI-side, not server-provided. Using last-seen per-conversation `updated_at`
instead (the brief's documented fallback): simpler, and each Conversation
row's own `last_ingested_at` vs. Bee's `updated_at` tells us what's stale.

Never gates on `state` (Phase 0.9 pitfall: state can stay CAPTURING
indefinitely after the transcript is already complete).
"""

import argparse
import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlmodel import select

from recall.bee.client import BeeClient
from recall.bee.process import BeeProxy
from recall.bee.stream import stream_events
from recall.config import settings
from recall.db import Conversation, Session, Utterance, create_db_and_tables, get_session

logger = logging.getLogger("recall.ingest")

SESSION_GAP_MINUTES = 10
POLL_INTERVAL_S = 60


def _to_dt(ms: int | float | None) -> datetime | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _parse_spoken_at(value) -> datetime | None:
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, (int, float)):
        return _to_dt(value)
    return None


def upsert_conversation(db, data: dict) -> Conversation:
    conv = db.get(Conversation, data["id"])
    if conv is None:
        conv = Conversation(id=data["id"])
    conv.conversation_uuid = data.get("conversation_uuid") or conv.conversation_uuid
    conv.start_time = _to_dt(data.get("start_time")) or conv.start_time
    conv.end_time = _to_dt(data.get("end_time")) or conv.end_time
    conv.short_summary = data.get("short_summary") or conv.short_summary
    conv.state = data.get("state")  # stored for visibility only -- never gated on
    db.add(conv)
    return conv


def upsert_utterance(db, conversation_id: int, conversation_uuid: str, u: dict) -> bool:
    """Returns True if a new row was inserted. Utterance content is treated
    as immutable once transcribed, so an existing id is left untouched."""
    if db.get(Utterance, u.get("id")) is not None:
        return False
    spoken_at = _parse_spoken_at(u.get("spoken_at"))
    if u.get("id") is None or spoken_at is None:
        return False
    db.add(
        Utterance(
            id=u["id"],
            conversation_id=conversation_id,
            conversation_uuid=conversation_uuid,
            text=u.get("text", ""),
            spoken_at=spoken_at,
            speaker=u.get("speaker"),
            created_at=_to_dt(u.get("created_at")),
        )
    )
    return True


def poll_once(client: BeeClient) -> dict:
    """One polling pass across all conversations, paginated via next_cursor.
    Upserts anything whose Bee `updated_at` is newer than our last ingest."""
    db = get_session()
    conv_touched = 0
    utterances_added = 0
    try:
        cursor = None
        while True:
            page = client.list_conversations(limit=50, cursor=cursor)
            conversations = page.get("conversations", [])
            if not conversations:
                break

            for data in conversations:
                conv_id = data["id"]
                existing = db.get(Conversation, conv_id)
                bee_updated_at = _to_dt(data.get("updated_at"))
                already_current = (
                    existing is not None
                    and existing.last_ingested_at is not None
                    and bee_updated_at is not None
                    and existing.last_ingested_at >= bee_updated_at
                )
                if already_current:
                    continue

                conv = upsert_conversation(db, data)
                transcript = client.get_transcript(conv_id)
                for u in transcript.get("transcript", []):
                    if upsert_utterance(db, conv_id, conv.conversation_uuid or "", u):
                        utterances_added += 1

                conv.last_ingested_at = datetime.now(UTC)
                db.add(conv)
                db.commit()
                conv_touched += 1

            cursor = page.get("next_cursor")
            if not cursor:
                break
    finally:
        rebuild_sessions(db)
        db.close()
    return {"conversations_touched": conv_touched, "utterances_added": utterances_added}


def rebuild_sessions(db) -> int:
    """Full rebuild of Session rows from spoken_at gaps > SESSION_GAP_MINUTES.

    Simple/correct for Phase 2 scale; a real incremental version (that
    preserves Session.id stability across rebuilds) can come later if
    something starts depending on stable session identity across runs --
    nothing does yet (Phase 3's question generator hasn't been built).
    """
    utterances = db.exec(select(Utterance).order_by(Utterance.spoken_at)).all()
    for s in db.exec(select(Session)).all():
        db.delete(s)
    db.commit()

    if not utterances:
        return 0

    gap = timedelta(minutes=SESSION_GAP_MINUTES)
    groups: list[list[Utterance]] = [[utterances[0]]]
    for u in utterances[1:]:
        if u.spoken_at - groups[-1][-1].spoken_at > gap:
            groups.append([])
        groups[-1].append(u)

    for group in groups:
        conv_ids = sorted({u.conversation_id for u in group})
        db.add(Session(start_at=group[0].spoken_at, end_at=group[-1].spoken_at, conversation_ids=conv_ids))
    db.commit()
    return len(groups)


async def consume_stream_live() -> None:
    """Live stream consumer for long-running mode: upserts new-utterance
    events as they arrive, deduping by utterance id against whatever
    polling has already ingested."""
    uuid_to_conv_id: dict[str, int] = {}
    db = get_session()
    try:
        async for event in stream_events():
            etype = event.get("type")
            if etype in ("new-conversation", "update-conversation"):
                conv = event.get("conversation") or {}
                if conv.get("id") and conv.get("conversation_uuid"):
                    uuid_to_conv_id[conv["conversation_uuid"]] = conv["id"]
                    upsert_conversation(db, conv)
                    db.commit()
            elif etype == "new-utterance":
                conv_uuid = event.get("conversation_uuid")
                u = event.get("utterance") or {}
                if not conv_uuid or u.get("id") is None:
                    continue
                conv_id = uuid_to_conv_id.get(conv_uuid)
                if conv_id is None:
                    continue  # can't resolve conversation yet -- next poll picks it up
                if upsert_utterance(db, conv_id, conv_uuid, u):
                    db.commit()
    finally:
        db.close()


async def run_long_running(client: BeeClient) -> None:
    async def poll_loop():
        loop = asyncio.get_running_loop()
        while True:
            await loop.run_in_executor(None, poll_once, client)
            await asyncio.sleep(POLL_INTERVAL_S)

    await asyncio.gather(poll_loop(), consume_stream_live())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    args = parser.parse_args()

    create_db_and_tables()

    with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
        if args.once:
            poll_once(client)
            db = get_session()
            conv_total = len(db.exec(select(Conversation)).all())
            utt_total = len(db.exec(select(Utterance)).all())
            session_total = len(db.exec(select(Session)).all())
            db.close()
            print(f"conversations={conv_total} utterances={utt_total} sessions={session_total}")
            return

        logger.info("starting long-running ingest loop (poll every %ds + live stream)", POLL_INTERVAL_S)
        asyncio.run(run_long_running(client))


if __name__ == "__main__":
    main()
