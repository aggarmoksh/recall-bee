"""Phase 5B Step 3: ready-tick latency probe (operator present, ~10 min).

Creates 3 "Recall: tick when you can talk" todos, polls REST every 5s for
completed:true, and measures detection latency from the moment the
operator actually ticks it (they press Enter the instant they tap it) to
the moment our poll observes completed:true. Also records whether they
ticked from the notification banner or inside the Bee app, to see if that
affects latency. TEST + DIAGNOSE ONLY -- no recall/ code touched; cleans
up its own test todos via the verified DELETE endpoint.

Usage: python -m phase5.tick_probe
"""

import time
from datetime import UTC, datetime
from pathlib import Path

from recall.bee.client import BeeClient
from recall.bee.process import BeeProxy
from recall.config import settings

OUT_PATH = Path(__file__).parent / "tick_probe.md"
POLL_INTERVAL_S = 5
NUM_TRIALS = 3


def run_trial(client: BeeClient, trial_num: int) -> dict:
    text = f"Recall: tick when you can talk (test {trial_num})"
    resp = client.create_todo(text)
    todo_id = resp["id"]
    print(f"\n--- Trial {trial_num} --- created todo {todo_id}: {text!r}")

    method = input("How will you tick it this trial -- (n)otification or (a)pp? ").strip().lower()
    method_label = "notification" if method.startswith("n") else "app"

    input("Go tick it now. Press Enter the INSTANT you tap it... ")
    tick_time = datetime.now(UTC)

    while True:
        todo = client.get_todo(todo_id)
        if todo.get("completed"):
            detected_at = datetime.now(UTC)
            break
        time.sleep(POLL_INTERVAL_S)

    latency_s = (detected_at - tick_time).total_seconds()
    print(f"  -> detected {latency_s:.1f}s after you ticked it")

    client.delete_todo(todo_id)

    return {"trial": trial_num, "method": method_label, "latency_s": round(latency_s, 1)}


def write_report(results: list[dict]) -> None:
    lines = ["# Phase 5B Step 3 -- Ready-tick latency\n"]
    lines.append("| Trial | Tick method | Detection latency (s) |")
    lines.append("|---|---|---|")
    for r in results:
        lines.append(f"| {r['trial']} | {r['method']} | {r['latency_s']} |")

    hits = sum(1 for r in results if r["latency_s"] <= 15)
    lines.append(f"\n{hits}/{len(results)} detected within 15s (criterion: {NUM_TRIALS}/{NUM_TRIALS}).")

    methods = {r["method"] for r in results}
    if len(methods) > 1:
        for m in sorted(methods):
            vals = [r["latency_s"] for r in results if r["method"] == m]
            lines.append(f"- {m}: {vals}")

    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH}")


def main() -> None:
    results = []
    with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
        for trial_num in range(1, NUM_TRIALS + 1):
            results.append(run_trial(client, trial_num))

    write_report(results)


if __name__ == "__main__":
    main()
