"""Phase 2 Step 2: explain-back reliability gate (operator present, ~45 min).

Uses recall/bee (process, client, stream) and recall/matcher -- no ad-hoc
subprocess code. Loads the 10 lecture questions from phase0/09_questions.md,
dispatches them as todos 4 minutes apart, records the operator's spoken
explain-back answers via Enter-press timing, then reconciles against REST
conversation history for anything the live stream missed.

Run this yourself, directly, in your own terminal:
    .venv/Scripts/python.exe phase2/explain_gate.py
"""

import json
import re
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recall.bee.client import BeeClient
from recall.bee.process import BeeProxy, BeeStream, find_bee_processes
from recall.bee.stream import parse_line
from recall.config import settings
from recall.matcher import Utterance, match_answer_window

HERE = Path(__file__).parent
PRIVATE_DIR = HERE.parent / "private"
QUESTIONS_MD = HERE.parent / "phase0" / "09_questions.md"
STREAM_RAW_PATH = PRIVATE_DIR / "phase2_stream_raw.ndjson"
TRIALS_FULL_PATH = PRIVATE_DIR / "phase2_trials_full.json"  # unredacted, private
RESULTS_MD = HERE / "explain_gate_results.md"

START_OFFSET_S = 120  # first alarm 2 min from now
SPACING_S = 240  # 4 min apart
TAIL_BUFFER_S = 600  # after last speech_end, wait 10 min before reconciling
WINDOW_PAD_S = 15

PRIVATE_DIR.mkdir(exist_ok=True)


def load_questions() -> list[str]:
    text = QUESTIONS_MD.read_text(encoding="utf-8")
    questions = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or not cells[0].isdigit():
            continue
        questions.append(cells[1])
    if len(questions) != 10:
        raise RuntimeError(f"expected 10 questions from {QUESTIONS_MD}, parsed {len(questions)}")
    return questions


def extract_utterance(event: dict) -> tuple[int, str, datetime] | None:
    if event.get("type") != "new-utterance":
        return None
    u = event.get("utterance") or {}
    uid = u.get("id")
    text = u.get("text")
    spoken_at_raw = u.get("spoken_at")
    if uid is None or text is None or spoken_at_raw is None:
        return None
    spoken_at = datetime.fromisoformat(spoken_at_raw.replace("Z", "+00:00"))
    return uid, text, spoken_at


def main() -> None:
    tz = ZoneInfo(settings.timezone)

    print("[gate] pre-flight orphan check...")
    orphans = find_bee_processes()
    if orphans:
        print(f"[gate] WARNING: {len(orphans)} pre-existing bee process(es): {[o['pid'] for o in orphans]}")

    questions = load_questions()
    print(f"[gate] loaded {len(questions)} questions from {QUESTIONS_MD.name}")

    trials: dict[int, dict] = {}
    with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
        now = datetime.now(tz)
        for i, question in enumerate(questions, start=1):
            offset = START_OFFSET_S + (i - 1) * SPACING_S
            alarm_at = now + timedelta(seconds=offset)
            text = f"Recall: {question}"
            resp = client.create_todo(text, alarm_at=alarm_at.isoformat(timespec="seconds"))
            trials[i] = {
                "trial": i,
                "question": question,
                "todo_id": resp.get("id"),
                "alarm_at_iso": alarm_at.isoformat(timespec="seconds"),
                "alarm_at_epoch": alarm_at.timestamp(),
            }
            print(f"[gate] trial {i}: id={resp.get('id')}, alarm {alarm_at.strftime('%H:%M:%S %Z')}")

    print()
    print("[gate] Full schedule:")
    for i in sorted(trials):
        at = datetime.fromtimestamp(trials[i]["alarm_at_epoch"], tz)
        print(f"  Trial {i}: {at.strftime('%H:%M:%S %Z')} -- {trials[i]['question']}")
    print()

    # Live utterance pool + receive-time tracking, populated by the stream reader thread
    live_utterances: list[Utterance] = []
    live_recv_by_id: dict[int, float] = {}
    pool_lock = threading.Lock()

    raw_out = open(STREAM_RAW_PATH, "w", encoding="utf-8")
    stream_cm = BeeStream(types="all")
    stream_proc = stream_cm.__enter__()
    start_monotonic = time.monotonic()

    def reader():
        for line in stream_proc.stdout:
            recv_wall = time.time()
            recv_monotonic = time.monotonic() - start_monotonic
            raw_out.write(json.dumps({"recv_monotonic_s": recv_monotonic, "recv_wall": recv_wall, "raw": line.rstrip("\n")}) + "\n")
            raw_out.flush()
            event = parse_line(line)
            if event is None:
                continue
            parsed = extract_utterance(event)
            if parsed is None:
                continue
            uid, text, spoken_at = parsed
            with pool_lock:
                live_utterances.append(Utterance(id=uid, text=text, spoken_at=spoken_at))
                live_recv_by_id[uid] = recv_wall

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    print("For each trial: wait for the prompt, say \"Recall answer\" and explain continuously")
    print("for 45-60s. Press Enter at speech START, then press Enter again at speech END.")
    print()

    try:
        for i in sorted(trials):
            t = trials[i]
            at = datetime.fromtimestamp(t["alarm_at_epoch"], tz)
            input(f">>> Trial {i} (Q: {t['question']}) -- expected prompt ~{at.strftime('%H:%M:%S %Z')}. Press Enter when you START speaking... ")
            t["speech_start_wall"] = time.time()
            input(f"    ...press Enter when you FINISH speaking for trial {i}... ")
            t["speech_end_wall"] = time.time()
            print(f"    logged speech window for trial {i}.")
    except KeyboardInterrupt:
        print("[gate] Ctrl-C received, wrapping up early...")

    last_speech_end = max((t.get("speech_end_wall", t["alarm_at_epoch"]) for t in trials.values()), default=time.time())
    wait_until = last_speech_end + TAIL_BUFFER_S
    remaining = wait_until - time.time()
    if remaining > 0:
        print(f"[gate] Listening {remaining:.0f}s more (10 min past last speech_end) before reconciling...")
        try:
            time.sleep(remaining)
        except KeyboardInterrupt:
            print("[gate] Ctrl-C during tail wait, moving to reconciliation now...")

    print("[gate] stopping stream...")
    stream_cm.__exit__(None, None, None)
    raw_out.close()
    reader_thread.join(timeout=5)

    # Reconciliation pass: pull REST conversation history, merge in any utterances
    # the live stream missed (dedupe by utterance id).
    print("[gate] reconciling against REST conversation history...")
    all_utterances: dict[int, Utterance] = {u.id: u for u in live_utterances}
    with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
        conversations = client.list_conversations(limit=20).get("conversations", [])
        earliest_alarm = min(t["alarm_at_epoch"] for t in trials.values())
        relevant_conv_ids = [
            c["id"] for c in conversations
            if c.get("start_time") is None or c["start_time"] / 1000 >= earliest_alarm - 300
        ]
        for conv_id in relevant_conv_ids:
            transcript = client.get_transcript(conv_id)
            for u in transcript.get("transcript", []):
                uid = u.get("id")
                if uid is None or uid in all_utterances:
                    continue
                spoken_at_ms = u.get("spoken_at")
                spoken_at = datetime.fromtimestamp(spoken_at_ms / 1000, tz=UTC) if spoken_at_ms else None
                if spoken_at is None:
                    continue
                all_utterances[uid] = Utterance(id=uid, text=u.get("text", ""), spoken_at=spoken_at)

    utterance_pool = list(all_utterances.values())
    print(f"[gate] utterance pool: {len(live_utterances)} live + {len(utterance_pool) - len(live_utterances)} reconciled = {len(utterance_pool)} total")

    # Match each trial's answer window
    for i in sorted(trials):
        t = trials[i]
        if "speech_start_wall" not in t or "speech_end_wall" not in t:
            t["match"] = None
            continue
        speech_start = datetime.fromtimestamp(t["speech_start_wall"], tz=UTC)
        speech_end = datetime.fromtimestamp(t["speech_end_wall"], tz=UTC)
        result = match_answer_window(utterance_pool, speech_start, speech_end, pad_seconds=WINDOW_PAD_S)
        recv_times = [live_recv_by_id[u.id] for u in result.utterances if u.id in live_recv_by_id]
        t["match"] = {
            "captured": len(result.utterances) > 0,
            "num_utterances": len(result.utterances),
            "trigger_found": result.trigger_found,
            "raw_text": result.raw_text,
            "utterance_ids": [u.id for u in result.utterances],
            "first_arrival_latency_s": (min(recv_times) - t["speech_end_wall"]) if recv_times else None,
            "last_arrival_latency_s": (max(recv_times) - t["speech_end_wall"]) if recv_times else None,
            "any_reconciled_only": len(recv_times) < len(result.utterances),
        }

    TRIALS_FULL_PATH.write_text(json.dumps(trials, indent=2, default=str), encoding="utf-8")
    print(f"[gate] wrote full (unredacted) data to {TRIALS_FULL_PATH}")

    # Redacted summary for git
    lines = [
        "# Phase 2 Step 2 -- explain-back gate results (redacted)",
        "",
        "Full text lives in private/phase2_trials_full.json (gitignored). This file has word counts and first 8 words only, per the redaction rule.",
        "",
        "| Trial | Captured y/n | # utterances | Trigger found y/n | First arrival latency (s) | Last arrival latency (s) | Words captured | First 8 words | Usable to grade (operator fills in) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i in sorted(trials):
        t = trials[i]
        m = t.get("match")
        if m is None:
            lines.append(f"| {i} | n (no speech window recorded) | - | - | - | - | - | - | |")
            continue
        words = m["raw_text"].split()
        first_8 = " ".join(words[:8]) + ("..." if len(words) > 8 else "")
        fa = f"{m['first_arrival_latency_s']:.1f}" if m["first_arrival_latency_s"] is not None else "N/A"
        la = f"{m['last_arrival_latency_s']:.1f}" if m["last_arrival_latency_s"] is not None else "N/A"
        lines.append(
            f"| {i} | {'y' if m['captured'] else 'n'} | {m['num_utterances']} | {'y' if m['trigger_found'] else 'n'} | {fa} | {la} | {len(words)} | {first_8} | |"
        )

    RESULTS_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[gate] wrote redacted results to {RESULTS_MD}")
    print("[gate] done.")


if __name__ == "__main__":
    main()
