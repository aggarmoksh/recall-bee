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

RECONNECT_BACKOFF_BASE_S = 1.0
RECONNECT_BACKOFF_MAX_S = 30.0


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
    backoff = RECONNECT_BACKOFF_BASE_S
    while True:
        warn_on_orphans()
        bee_exe = resolve_bee_exe()
        proc = await asyncio.create_subprocess_exec(
            bee_exe, "stream", "--json", "--types", types,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            assert proc.stdout is not None
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break  # subprocess ended
                event = parse_line(raw.decode("utf-8", errors="replace"))
                if event is None:
                    continue
                backoff = RECONNECT_BACKOFF_BASE_S  # reset after any successful event
                yield event
        finally:
            kill_tree(proc.pid)

        logger.warning("bee stream subprocess ended, reconnecting in %.1fs", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX_S)
