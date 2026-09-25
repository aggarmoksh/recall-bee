"""Phase 1 live smoke test (manual, operator present).

Starts the bee proxy via recall.bee.process, calls get_me and
list_conversations (count only), creates one todo with alarm_at = now+2min,
stops the proxy, and verifies no orphan bee.exe remains. Writes output to
phase1/smoke_output.txt with personal data redacted.

Run with the venv active:
    python scripts/smoke.py
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recall.bee.client import BeeClient
from recall.bee.process import BeeProxy, find_bee_processes
from recall.config import settings

OUT_PATH = Path(__file__).resolve().parents[1] / "phase1" / "smoke_output.txt"


def main() -> None:
    lines = []

    def log(msg: str) -> None:
        print(msg)
        lines.append(msg)

    log(f"[smoke] starting bee proxy on port {settings.bee_proxy_port}...")
    with BeeProxy(port=settings.bee_proxy_port) as proxy:
        log(f"[smoke] proxy ready at {proxy.base_url}")
        with BeeClient(proxy.base_url) as client:
            me = client.get_me()
            log(f"[smoke] get_me() OK -- keys: {sorted(me.keys())} (values redacted)")

            conversations = client.list_conversations(limit=20)
            count = len(conversations.get("conversations", []))
            log(f"[smoke] list_conversations() OK -- {count} conversation(s) returned")

            tz = ZoneInfo(settings.timezone)
            alarm_at = (datetime.now(tz) + timedelta(minutes=2)).isoformat(timespec="seconds")
            todo = client.create_todo("Recall Phase 1 smoke test", alarm_at=alarm_at)
            log(f"[smoke] create_todo() OK -- id={todo.get('id')}, alarm_at={alarm_at}")

    log("[smoke] proxy stopped, checking for orphans...")
    orphans = find_bee_processes()
    if orphans:
        log(f"[smoke] FAIL: {len(orphans)} orphaned bee process(es) still running: {[o['pid'] for o in orphans]}")
    else:
        log("[smoke] OK: no orphaned bee processes.")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[smoke] wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
