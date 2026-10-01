"""Phase 5B Step 2: spoken-start gate probe (operator present, ~25 min).

Tests whether a spoken start phrase reliably registers as a `new-utterance`
stream event, as a candidate addition to the tick-todo start mechanism in
the v4 delivery model (AGENT.md). TEST + DIAGNOSE ONLY -- does not touch
any recall/ module logic, only reads from recall.bee.stream.

10 trials, ~2 min apart. Trials 1-5 say variant A, trials 6-10 say variant
B. The operator presses Enter immediately after finishing each phrase;
detection is a fuzzy match on "recall" AND "session" (separately, any
word in the captured utterance) within 90s of that Enter press. After
each trial the operator is asked whether Bee's own on-phone assistant
intercepted/responded to the phrase.

Usage: python -m phase5.start_probe
"""

import asyncio
import difflib
import json
from datetime import UTC, datetime
from pathlib import Path

from recall.bee.stream import stream_events

OUT_PATH = Path(__file__).parent / "start_gate.md"
RAW_LOG_PATH = Path(__file__).parent / "start_probe_raw.ndjson"

DETECT_WINDOW_S = 90
FUZZY_THRESHOLD = 0.75

VARIANTS = {
    "A": "Hey Bee, let's do a Recall session, I'm ready for my questions.",
    "B": "Okay Recall, let's do a session, I'm ready for my questions.",
}

TRIAL_PLAN = [(i, "A" if i <= 5 else "B") for i in range(1, 11)]


def _word_fuzzy_match(text: str, target: str, threshold: float = FUZZY_THRESHOLD) -> bool:
    words = text.lower().split()
    return any(difflib.SequenceMatcher(None, w.strip(".,!?\"'"), target).ratio() >= threshold for w in words)


def _detects_start(text: str) -> bool:
    return _word_fuzzy_match(text, "recall") and _word_fuzzy_match(text, "session")


async def _drain(queue: asyncio.Queue) -> None:
    """Discard anything already queued (stale, from before this trial)."""
    while not queue.empty():
        queue.get_nowait()


async def run_trial(trial_num: int, variant: str, queue: asyncio.Queue) -> dict:
    phrase = VARIANTS[variant]
    print(f"\n--- Trial {trial_num} (variant {variant}) ---")
    print(f'Say: "{phrase}"')

    await _drain(queue)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, input, "Press Enter the instant you finish speaking... ")
    speech_end = datetime.now(UTC)

    deadline = speech_end.timestamp() + DETECT_WINDOW_S
    detected = False
    latency_s = None
    captured_text = ""

    while True:
        remaining = deadline - datetime.now(UTC).timestamp()
        if remaining <= 0:
            break
        try:
            event = await asyncio.wait_for(queue.get(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if event.get("type") != "new-utterance":
            continue
        u = event.get("utterance") or {}
        text = u.get("text", "")
        if not captured_text:
            captured_text = text  # first utterance seen this trial, match or not
        if _detects_start(text):
            detected = True
            latency_s = (datetime.now(UTC) - speech_end).total_seconds()
            captured_text = text
            break

    assistant_raw = await loop.run_in_executor(None, input, "Did Bee's own assistant respond on the phone? (y/n): ")

    return {
        "trial": trial_num,
        "variant": variant,
        "detected": detected,
        "latency_s": round(latency_s, 1) if latency_s is not None else None,
        "captured_text_first_12_words": " ".join(captured_text.split()[:12]),
        "assistant_responded": assistant_raw.strip().lower().startswith("y"),
    }


def write_report(results: list[dict]) -> None:
    lines = ["# Phase 5B Step 2 -- Spoken-start gate\n"]
    lines.append("| Trial | Variant | Detected | Latency (s) | Captured (first 12 words) | Assistant intercepted |")
    lines.append("|---|---|---|---|---|---|")
    for r in results:
        lines.append(
            f"| {r['trial']} | {r['variant']} | {'y' if r['detected'] else 'n'} | "
            f"{r['latency_s'] if r['latency_s'] is not None else '-'} | "
            f"{r['captured_text_first_12_words']} | {'y' if r['assistant_responded'] else 'n'} |"
        )

    lines.append("")
    for variant in ("A", "B"):
        subset = [r for r in results if r["variant"] == variant]
        hits = sum(1 for r in subset if r["detected"])
        lines.append(f"Variant {variant}: {hits}/{len(subset)} detected")

    total_hits = sum(1 for r in results if r["detected"])
    lines.append(f"\nOverall: {total_hits}/{len(results)} detected")
    lines.append("\n(Criterion: >=8/10 overall, OR >=4/5 for one variant -- that variant gets adopted. Reported here, not auto-decided.)")

    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH}")


async def main() -> None:
    results = []
    queue: asyncio.Queue = asyncio.Queue()

    async def pump_stream():
        async for event in stream_events():
            await queue.put(event)
            RAW_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(RAW_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str) + "\n")

    pump_task = asyncio.create_task(pump_stream())
    try:
        for trial_num, variant in TRIAL_PLAN:
            result = await run_trial(trial_num, variant, queue)
            results.append(result)
            print(f"  -> detected={result['detected']} latency={result['latency_s']}s")
    finally:
        pump_task.cancel()
        try:
            await pump_task
        except asyncio.CancelledError:
            pass

    write_report(results)


if __name__ == "__main__":
    asyncio.run(main())
