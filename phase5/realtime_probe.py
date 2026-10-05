"""Phase 5D Step 2: realtime-transcription probe (operator present, ~15 min).

TEST ONLY -- does not touch any recall/ module logic. Uses recall.bee.client
and recall.bee.stream as-is.

Hypothesis (from reading docs.bee.computer + the bee-cli source): each
conversation's `transcriptions` list can carry entries with a `realtime`
true/false flag, and a realtime entry may become readable via
GET /v1/conversations/:id while the conversation is still CAPTURING --
which would cut answer latency without ever needing "Process now".

What this script does, concurrently:
1. Every 10s: list recent conversations; for any not in a terminal state,
   fetch GET /v1/conversations/:id directly and log, per transcription:
   id, realtime flag, utterance count, newest utterance's text (first 10
   words) + spoken_at, and the conversation's own state.
2. Runs the live stream and logs receive time + state/text for every
   new-utterance and update-conversation event.

Everything is appended to phase5/realtime_probe_raw.ndjson with a wall-clock
`received_at`, tagged `"source": "poll"` or `"source": "stream"`. The
operator drives 3 trials manually (see phase5/realtime_probe.md for the
protocol); this script just logs continuously in the background for the
whole window -- it does not prompt or gate on trial boundaries itself.

Usage: python -m phase5.realtime_probe  (Ctrl-C to stop)
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from recall.bee.client import BeeClient
from recall.bee.process import bee_proxy
from recall.bee.stream import stream_events

RAW_LOG_PATH = Path(__file__).parent / "realtime_probe_raw.ndjson"
POLL_INTERVAL_S = 10
TERMINAL_STATES = {"COMPLETED", "DONE", "FINISHED"}  # best guess; logged regardless either way


def _append(record: dict) -> None:
    record["received_at"] = datetime.now(UTC).isoformat()
    RAW_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RAW_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _first_n_words(text: str, n: int = 10) -> str:
    words = (text or "").split()
    return " ".join(words[:n])


async def poll_loop(client: BeeClient) -> None:
    loop = asyncio.get_running_loop()
    while True:
        try:
            page = await loop.run_in_executor(None, client.list_conversations, 10)
        except Exception as e:  # noqa: BLE001 -- probe script, log and keep going
            _append({"source": "poll", "event": "list_conversations_error", "error": str(e)})
            await asyncio.sleep(POLL_INTERVAL_S)
            continue

        for conv in page.get("conversations", []):
            conv_id = conv.get("id")
            state = conv.get("state")
            if state in TERMINAL_STATES:
                continue
            try:
                detail = await loop.run_in_executor(None, client._request, "GET", f"/v1/conversations/{conv_id}")
            except Exception as e:  # noqa: BLE001
                _append({"source": "poll", "event": "get_conversation_error", "conversation_id": conv_id, "error": str(e)})
                continue
            conversation = detail.get("conversation", detail)
            transcriptions = conversation.get("transcriptions") or []
            record = {
                "source": "poll",
                "event": "conversation_detail",
                "conversation_id": conv_id,
                "state": conversation.get("state"),
                "transcriptions": [],
            }
            for t in transcriptions:
                utterances = t.get("utterances") or []
                newest = max(utterances, key=lambda u: u.get("spoken_at") or 0, default=None)
                record["transcriptions"].append(
                    {
                        "transcription_id": t.get("id"),
                        "realtime": t.get("realtime"),
                        "utterance_count": len(utterances),
                        "newest_utterance_text": _first_n_words(newest.get("text")) if newest else None,
                        "newest_utterance_spoken_at": newest.get("spoken_at") if newest else None,
                    }
                )
            _append(record)

        await asyncio.sleep(POLL_INTERVAL_S)


async def stream_loop() -> None:
    async for event in stream_events():
        etype = event.get("type")
        if etype == "new-utterance":
            u = event.get("utterance") or {}
            _append(
                {
                    "source": "stream",
                    "event": "new-utterance",
                    "conversation_uuid": event.get("conversation_uuid"),
                    "text": _first_n_words(u.get("text")),
                    "spoken_at": u.get("spoken_at"),
                }
            )
        elif etype in ("new-conversation", "update-conversation"):
            conv = event.get("conversation") or {}
            _append(
                {
                    "source": "stream",
                    "event": etype,
                    "conversation_id": conv.get("id"),
                    "conversation_uuid": conv.get("conversation_uuid"),
                    "state": conv.get("state"),
                }
            )
        # heartbeats / unknown shapes: ignored, not logged (keeps the file readable)


async def main() -> None:
    with bee_proxy() as proxy, BeeClient(proxy.base_url) as client:
        print(f"Logging to {RAW_LOG_PATH}. Ctrl-C to stop.")
        await asyncio.gather(poll_loop(client), stream_loop())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped")
