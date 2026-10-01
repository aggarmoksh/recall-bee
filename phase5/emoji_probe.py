"""Phase 5B Step 4: emoji + length check (~3 min).

Creates 2 todos with alarm_at ~1 min out, using the exact copy style v4's
stings/session-report todos will use. The operator reports back whether
the emoji rendered on Watch and iPhone -- this script only creates the
todos and records what the operator says; it does not poll or detect
anything itself. TEST + DIAGNOSE ONLY -- no recall/ code touched.

Usage: python -m phase5.emoji_probe
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from recall.bee.client import BeeClient
from recall.bee.process import BeeProxy
from recall.config import settings

OUT_PATH = Path(__file__).parent / "emoji_probe.md"
ALARM_DELAY_S = 60

TEXTS = [
    "🐝 6 questions are buzzing for you",
    "Recall: 4/5 ✓ · 🔥 3-day streak",
]


def main() -> None:
    results = []
    with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
        for text in TEXTS:
            alarm_at = datetime.now(UTC) + timedelta(seconds=ALARM_DELAY_S)
            resp = client.create_todo(text, alarm_at=alarm_at.isoformat(timespec="seconds"))
            print(f"\ncreated todo {resp['id']}: {text!r}  (alarm in ~{ALARM_DELAY_S}s)")
            input("Press Enter once it has buzzed and you've seen it on both devices... ")
            watch = input("Did the emoji render correctly on the Watch? (y/n/no-watch-notification): ").strip()
            phone = input("Did the emoji render correctly on the iPhone? (y/n): ").strip()
            results.append({"text": text, "todo_id": resp["id"], "watch": watch, "phone": phone})
            client.delete_todo(resp["id"])

    lines = ["# Phase 5B Step 4 -- Emoji + length check\n"]
    lines.append("| Text | Watch rendering | iPhone rendering |")
    lines.append("|---|---|---|")
    for r in results:
        lines.append(f"| {r['text']} | {r['watch']} | {r['phone']} |")
    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
