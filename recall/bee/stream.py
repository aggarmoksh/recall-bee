"""Async consumer for `bee stream --json`.

Yields parsed events one at a time, tolerant of unknown/heartbeat lines
(heartbeats like {"timestamp": ...} have no "type" field and are yielded
as-is -- callers decide what to do with them). Reconnects with exponential
backoff if the subprocess ends.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from recall.bee.process import kill_tree, resolve_bee_exe, warn_on_orphans

logger = logging.getLogger("recall.bee.stream")

# Phase 5G Step 4 (AGENT.md, 2026-10-07): fixed 5s/15s/60s-cap schedule,
# replacing the old 1s-doubling-to-30s backoff -- chosen to match the
# daemon's other supervisor backoffs (recall.run._supervised) so every
# reconnect path in the system behaves the same way.
RECONNECT_BACKOFF_STEPS_S = [5.0, 15.0, 60.0]

# Phase 5G Step 4: asyncio.StreamReader's default internal buffer cap is
# 64KiB -- the 2-day check's second crash (FRICTION_LOG.md, 2026-10-07)
# was `ValueError: Separator is not found, and chunk exceed the limit` from
# a single stream line exceeding that, propagating out of readline()
# uncaught and killing the whole daemon. Raised well past anything a
# single JSON event should ever need.
STREAM_READ_BUFFER_LIMIT = 16 * 1024 * 1024


def parse_line(line: str) -> dict | None:
    """Pure parser: one raw stream line -> event dict, or None if not parseable.

    Never raises. Unknown/heartbeat shapes are returned as-is; only invalid
    JSON (or a blank line) yields None.
    """
    text = line.strip()
    if not text:
        return None
    try:
        event = json.loads(text)
    except json.JSONDecodeError:
        logger.debug("non-JSON line from bee stream, skipping: %r", text)
        return None
    if not isinstance(event, dict):
        return None
    return event


async def stream_events(types: str = "all") -> AsyncIterator[dict]:
    backoff_index = 0
    while True:
        warn_on_orphans()
        bee_exe = resolve_bee_exe()
        proc = await asyncio.create_subprocess_exec(
            bee_exe, "stream", "--json", "--types", types,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=STREAM_READ_BUFFER_LIMIT,
        )
        try:
            assert proc.stdout is not None
            while True:
                try:
                    raw = await proc.stdout.readline()
                except Exception as e:
                    # Never let a read failure (e.g. a line still over the
                    # raised limit, or a decode-adjacent asyncio error)
                    # escape this generator -- it would otherwise propagate
                    # out of consume_stream_live() and, pre-Phase-5G, the
                    # whole daemon process (FRICTION_LOG.md Incident 2).
                    logger.warning("bee stream read failed (%r), reconnecting", e)
                    break
                if not raw:
                    break  # subprocess ended
                event = parse_line(raw.decode("utf-8", errors="replace"))
                if event is None:
                    continue
                backoff_index = 0  # reset after any successful event
                yield event
        finally:
            kill_tree(proc.pid)

        backoff = RECONNECT_BACKOFF_STEPS_S[min(backoff_index, len(RECONNECT_BACKOFF_STEPS_S) - 1)]
        logger.warning("bee stream subprocess ended, reconnecting in %.1fs", backoff)
        await asyncio.sleep(backoff)
        backoff_index += 1
