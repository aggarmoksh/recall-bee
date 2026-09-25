"""Phase 3 Step 5: short-answer capture test (operator present, ~20 min).

Reuses the same recall/bee + recall/matcher infrastructure as
phase2/explain_gate.py (no ad-hoc subprocess code), adapted for this test:
5 of the real generated lecture questions (session 5), alarms 3 minutes
apart, and no trigger phrase required (matches the Phase 3 matcher update --
matching is by time window from alarm_at, trigger is a confidence flag
only). Protocol: answer in 15-20 seconds, Enter at speech start and end.

Run this yourself, directly, in your own terminal:
    .venv/Scripts/python.exe phase3/short_answer_test.py
"""

import json
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import select

from recall.bee.client import BeeClient
from recall.bee.process import BeeProxy, BeeStream, find_bee_processes
from recall.bee.stream import parse_line
from recall.config import settings
from recall.db import Question, get_session
from recall.matcher import Utterance, match_answer_window

HERE = Path(__file__).parent
PRIVATE_DIR = HERE.parent / "private"
STREAM_RAW_PATH = PRIVATE_DIR / "phase3_short_answer_stream_raw.ndjson"
TRIALS_FULL_PATH = PRIVATE_DIR / "phase3_short_answer_trials_full.json"  # unredacted, private
RESULTS_MD = HERE / "short_answer_results.md"

SESSION_ID = 5  # the lecture session
NUM_TRIALS = 5
START_OFFSET_S = 90  # first alarm 1.5 min from now
SPACING_S = 180  # 3 min apart
TAIL_BUFFER_S = 300  # after last speech_end, wait 5 min before reconciling

PRIVATE_DIR.mkdir(exist_ok=True)


def load_questions() -> list[Question]:
    db = get_session()
    try:
        questions = db.exec(
            select(Question).where(Question.session_id == SESSION_ID).order_by(Question.id).limit(NUM_TRIALS)
        ).all()
        if len(questions) < NUM_TRIALS:
            raise RuntimeError(f"expected >= {NUM_TRIALS} questions for session {SESSION_ID}, found {len(questions)}")
        return questions
    finally:
        db.close()


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

    print("[test] pre-flight orphan check...")
    orphans = find_bee_processes()
    if orphans:
        print(f"[test] WARNING: {len(orphans)} pre-existing bee process(es): {[o['pid'] for o in orphans]}")

    questions = load_questions()
    print(f"[test] loaded {len(questions)} questions from session {SESSION_ID}")

    trials: dict[int, dict] = {}
    with BeeProxy(port=settings.bee_proxy_port) as proxy, BeeClient(proxy.base_url) as client:
        now = datetime.now(tz)
        for i, q in enumerate(questions, start=1):
            offset = START_OFFSET_S + (i - 1) * SPACING_S
            alarm_at = now + timedelta(seconds=offset)
            text = f"Recall: {q.text}"
            resp = client.create_todo(text, alarm_at=alarm_at.isoformat(timespec="seconds"))
            trials[i] = {
                "trial": i,
                "question_id": q.id,
                "question": q.text,
                "todo_id": resp.get("id"),
                "alarm_at_iso": alarm_at.isoformat(timespec="seconds"),
                "alarm_at_epoch": alarm_at.timestamp(),
            }
            print(f"[test] trial {i}: id={resp.get('id')}, alarm {alarm_at.strftime('%H:%M:%S %Z')}")

    print()
    print("[test] Full schedule:")
    for i in sorted(trials):
        at = datetime.fromtimestamp(trials[i]["alarm_at_epoch"], tz)
        print(f"  Trial {i}: {at.strftime('%H:%M:%S %Z')} -- {trials[i]['question']}")
    print()

    live_utterances: list[Utterance] = []
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

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    print("For each trial: wait for the prompt, then answer in 15-20 seconds. No trigger phrase")
    print("required. Press Enter at speech START, then press Enter again at speech END.")
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
        print("[test] Ctrl-C received, wrapping up early...")

    last_speech_end = max((t.get("speech_end_wall", t["alarm_at_epoch"]) for t in trials.values()), default=time.time())
    wait_until = last_speech_end + TAIL_BUFFER_S
    remaining = wait_until - time.time()
    if remaining > 0:
        print(f"[test] Listening {remaining:.0f}s more (5 min past last speech_end) before reconciling...")
        try:
            time.sleep(remaining)
        except KeyboardInterrupt:
            print("[test] Ctrl-C during tail wait, moving to reconciliation now...")

    print("[test] stopping stream...")
    stream_cm.__exit__(None, None, None)
    raw_out.close()
    reader_thread.join(timeout=5)

    print("[test] reconciling against REST conversation history...")
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
    print(f"[test] utterance pool: {len(live_utterances)} live + {len(utterance_pool) - len(live_utterances)} reconciled = {len(utterance_pool)} total")

    for i in sorted(trials):
        t = trials[i]
        alarm_at = datetime.fromtimestamp(t["alarm_at_epoch"], tz=UTC)
        result = match_answer_window(utterance_pool, alarm_at=alarm_at)
        t["match"] = {
            "captured": len(result.utterances) > 0,
            "num_utterances": len(result.utterances),
            "trigger_found": result.trigger_found,
            "raw_text": result.raw_text,
            "utterance_ids": [u.id for u in result.utterances],
        }

    TRIALS_FULL_PATH.write_text(json.dumps(trials, indent=2, default=str), encoding="utf-8")
    print(f"[test] wrote full (unredacted) data to {TRIALS_FULL_PATH}")

    lines = [
        "# Phase 3 Step 5 -- short-answer capture test results",
        "",
        f"5 trials, 3 min apart, session {SESSION_ID}'s lecture questions, no trigger phrase required. Full text in private/phase3_short_answer_trials_full.json (gitignored).",
        "",
        "| Trial | Question id | Captured y/n | # utterances | Trigger found y/n |",
        "|---|---|---|---|---|",
    ]
    captured_count = 0
    for i in sorted(trials):
        t = trials[i]
        m = t["match"]
        if m["captured"]:
            captured_count += 1
        lines.append(f"| {i} | {t['question_id']} | {'y' if m['captured'] else 'n'} | {m['num_utterances']} | {'y' if m['trigger_found'] else 'n'} |")
    lines.append("")
    lines.append(f"**Result: {captured_count}/{NUM_TRIALS} captured.** Pass bar: 4/5.")

    RESULTS_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[test] wrote {RESULTS_MD}")
    print("[test] done.")


if __name__ == "__main__":
    main()
