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
import re
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from recall.bee.client import BeeClient
from recall.bee.process import BeeProxy
from recall.bee.stream import stream_events
from recall.config import settings
from recall.db import Conversation, Session, Utterance, create_db_and_tables, get_session

logger = logging.getLogger("recall.ingest")

SESSION_GAP_MINUTES = 10
POLL_INTERVAL_S = 60
LIVE_POLL_INTERVAL_S = 5  # Phase 5E: fast poller for conversations still in flight
NON_FINAL_STATES = {"CAPTURING", "PROCESSING"}
FUZZY_DEDUPE_WINDOW_S = 2


def _to_dt(value: int | float | str | None) -> datetime | None:
    """Bee's REST poll responses use epoch-ms ints; the live stream's
    conversation/utterance payloads use ISO8601 strings instead (same
    REST-vs-stream field-format split already seen with todo events'
    alarmAt) -- accept either so a stream event's start_time/end_time/
    spoken_at doesn't crash upsert_conversation/upsert_utterance."""
    if value is None:
        return None
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _ensure_conversation_row(db, conv_id: int) -> None:
    """Guarantees a Conversation row exists for conv_id, tolerating a
    concurrent insert race between poll_once (60s) and the 5s live poller
    (Phase 5E Step 1) -- both can see a brand-new conversation for the
    first time around the same moment and both try to INSERT it, and
    whichever commits second hits a UNIQUE constraint violation (found
    live, Phase 5F Step 7). Commits this tiny insert-or-noop immediately
    and in isolation from the caller's own pending work (utterance
    inserts etc.), so a conflict here never rolls back unrelated data --
    only the other writer's already-committed row is lost, which is fine
    since it's empty at this point regardless of which writer "won"."""
    if db.get(Conversation, conv_id) is not None:
        return
    try:
        db.add(Conversation(id=conv_id))
        db.commit()
    except IntegrityError:
        db.rollback()  # the other writer won the race -- the row exists now


def upsert_conversation(db, data: dict) -> Conversation:
    _ensure_conversation_row(db, data["id"])
    conv = db.get(Conversation, data["id"])
    conv.conversation_uuid = data.get("conversation_uuid") or conv.conversation_uuid
    conv.start_time = _to_dt(data.get("start_time")) or conv.start_time
    conv.end_time = _to_dt(data.get("end_time")) or conv.end_time
    conv.short_summary = data.get("short_summary") or conv.short_summary
    conv.state = data.get("state")  # stored for visibility only -- never gated on
    db.add(conv)
    return conv


def _normalize_text(text: str) -> str:
    return re.sub(r"[^\w\s]", "", (text or "").lower()).strip()


def _find_fuzzy_duplicate(db, conversation_id: int, text: str, spoken_at: datetime) -> Utterance | None:
    """Phase 5E Step 1: the realtime and final (non-realtime) transcriptions
    assign DIFFERENT utterance ids to the same speech (confirmed live,
    FRICTION_LOG 2026-10-01/02), so id-only dedup isn't enough -- also
    check normalized text + spoken_at within +/-2s before inserting."""
    normalized = _normalize_text(text)
    if not normalized:
        return None
    window = timedelta(seconds=FUZZY_DEDUPE_WINDOW_S)
    candidates = db.exec(
        select(Utterance)
        .where(Utterance.conversation_id == conversation_id)
        .where(Utterance.spoken_at >= spoken_at - window)
        .where(Utterance.spoken_at <= spoken_at + window)
    ).all()
    for c in candidates:
        if _normalize_text(c.text) == normalized:
            return c
    return None


def upsert_utterance(db, conversation_id: int, conversation_uuid: str, u: dict, *, is_final: bool = False) -> bool:
    """Returns True if a new row was inserted. Dedupes by exact id first,
    then by normalized text + spoken_at +/-2s (Phase 5E Step 1). On a
    fuzzy-duplicate match coming from the final transcript, the existing
    row's text is upgraded in place (same id) rather than inserting a
    second row -- keeps any existing Answer.utterance_ids reference valid
    and never triggers a re-grade; the final text just becomes the
    evidence of record for that same utterance."""
    if db.get(Utterance, u.get("id")) is not None:
        return False
    spoken_at = _to_dt(u.get("spoken_at"))
    if u.get("id") is None or spoken_at is None:
        return False

    existing = _find_fuzzy_duplicate(db, conversation_id, u.get("text", ""), spoken_at)
    if existing is not None:
        if is_final and existing.text != u.get("text", ""):
            existing.text = u.get("text", "")
            db.add(existing)
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


def poll_once(client: BeeClient, on_new_utterance=None) -> dict:
    """One polling pass across all conversations, paginated via next_cursor.
    Upserts anything whose Bee `updated_at` is newer than our last ingest.

    `on_new_utterance`, if given, fires for every utterance actually
    inserted -- same contract as poll_live_conversations/consume_stream_live
    (Phase 5E Step 2). Needed as a fallback: if a conversation leaves
    CAPTURING/PROCESSING before either the 5s live poller or the stream
    manages to see it, THIS is the only path left that ever ingests it, and
    without this wiring a spoken start phrase in that conversation would
    silently never start a session (found live, FRICTION_LOG 2026-10-05)."""
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
                is_final = transcript.get("state") not in NON_FINAL_STATES
                for u in transcript.get("transcript", []):
                    if upsert_utterance(db, conv_id, conv.conversation_uuid or "", u, is_final=is_final):
                        utterances_added += 1
                        if on_new_utterance is not None:
                            spoken_at = _to_dt(u.get("spoken_at"))
                            on_new_utterance(u["id"], u.get("text", ""), spoken_at)

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
    """Incremental rebuild of Session rows from spoken_at gaps > SESSION_GAP_MINUTES.

    Matches groups to existing Session rows by start_at and updates
    end_at/conversation_ids in place -- a full delete-and-recreate (the
    Phase 2 version) would wipe out Phase 4A's session_type/approval_status
    classification on every re-ingest. A classified session (session_type
    is not None) is NEVER deleted, even if its group no longer matches
    exactly; only still-unclassified stale sessions get cleaned up.
    """
    utterances = db.exec(select(Utterance).order_by(Utterance.spoken_at)).all()
    existing_by_start = {s.start_at: s for s in db.exec(select(Session)).all()}

    if not utterances:
        return 0

    gap = timedelta(minutes=SESSION_GAP_MINUTES)
    groups: list[list[Utterance]] = [[utterances[0]]]
    for u in utterances[1:]:
        if u.spoken_at - groups[-1][-1].spoken_at > gap:
            groups.append([])
        groups[-1].append(u)

    matched_starts = set()
    for group in groups:
        start_at = group[0].spoken_at
        end_at = group[-1].spoken_at
        conv_ids = sorted({u.conversation_id for u in group})
        matched_starts.add(start_at)

        existing = existing_by_start.get(start_at)
        if existing is not None:
            existing.end_at = end_at
            existing.conversation_ids = conv_ids
            db.add(existing)
        else:
            db.add(Session(start_at=start_at, end_at=end_at, conversation_ids=conv_ids))

    for start_at, s in existing_by_start.items():
        if start_at not in matched_starts and s.session_type is None:
            db.delete(s)

    db.commit()
    return len(groups)


def _ingest_conversation_once(db, client: BeeClient, conv_id: int, on_new_utterance=None) -> int:
    """Fetches one conversation's current state + single-source transcript
    and upserts whatever's new. Returns utterances actually added. Shared
    by the 5s live poller and the immediate-fetch-on-processed stream
    handler (Phase 5E Step 1).

    `on_new_utterance`, if given, fires for every utterance actually
    inserted (Phase 5E Step 2: spoken-start detection runs on utterances
    from the poller AND the stream, whichever sees it first -- `upsert_
    utterance`'s own id/fuzzy dedup guarantees this only fires once per
    real utterance no matter which path wins the race)."""
    transcript = client.get_transcript(conv_id)
    conv = db.get(Conversation, conv_id)
    if conv is None:
        conv = Conversation(id=conv_id)
    conv.state = transcript.get("state")
    is_final = transcript.get("state") not in NON_FINAL_STATES
    added = 0
    for u in transcript.get("transcript", []):
        if upsert_utterance(db, conv_id, conv.conversation_uuid or "", u, is_final=is_final):
            added += 1
            if on_new_utterance is not None:
                spoken_at = _to_dt(u.get("spoken_at"))
                on_new_utterance(u["id"], u.get("text", ""), spoken_at)
    conv.last_ingested_at = datetime.now(UTC)
    db.add(conv)
    db.commit()
    return added


async def poll_live_conversations(client: BeeClient, on_new_utterance=None) -> None:
    """Phase 5E Step 1: 5s poller for conversations still CAPTURING/
    PROCESSING. This is what makes recall.session's 15s-quiet answer-done
    pacing viable at all -- the old 60s poll_once cycle is far too slow to
    notice speech has stopped within a 15s window."""
    loop = asyncio.get_running_loop()
    while True:
        db = get_session()
        try:
            page = await loop.run_in_executor(None, client.list_conversations, 10)
            for data in page.get("conversations", []):
                if data.get("state") not in NON_FINAL_STATES:
                    continue
                await loop.run_in_executor(None, _ingest_conversation_once, db, client, data["id"], on_new_utterance)
            rebuild_sessions(db)
        finally:
            db.close()
        await asyncio.sleep(LIVE_POLL_INTERVAL_S)


async def consume_stream_live(on_new_utterance=None, client: BeeClient | None = None) -> None:
    """Live stream consumer for long-running mode: upserts new-utterance
    events as they arrive, deduping by utterance id against whatever
    polling has already ingested.

    `on_new_utterance`, if given, is called as `on_new_utterance(utterance_id,
    text, spoken_at)` for every utterance actually inserted (not duplicates)
    -- Phase 5C's run.py uses this for live spoken-start-phrase detection,
    without this module needing to know anything about sessions.

    `client`, if given, is used to immediately fetch+ingest a conversation
    the instant its own `update-conversation` stream event reports a state
    that's no longer CAPTURING/PROCESSING (Phase 5E Step 1) -- rather than
    waiting for the next 5s poll tick to notice the final transcript."""
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
                    if etype == "update-conversation" and client is not None and conv.get("state") not in NON_FINAL_STATES:
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(None, _ingest_conversation_once, db, client, conv["id"], on_new_utterance)
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
                    if on_new_utterance is not None:
                        spoken_at = _to_dt(u.get("spoken_at"))
                        on_new_utterance(u["id"], u.get("text", ""), spoken_at)
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
