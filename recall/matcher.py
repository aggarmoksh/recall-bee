"""Production answer matcher (updated 2026-09-28 -- see AGENT.md "Schema
decisions"): trigger phrase forgotten 3/10 times in the Phase 2 gate, so it
is now a confidence flag only, never a gate on matching.

The answer window starts at the dispatch's `alarm_at` (not a spoken
speech-start/end -- those only exist as gate instrumentation, not in
production) and stays open, accepting utterances, as long as each new one
arrives within `tail_close_s` of the previous one already in the window.
Before the first utterance arrives there is no tail-close constraint (the
wearer may take a while to notice the prompt and start answering), so the
window effectively stays open until the hard cap in that case. The whole
window is hard-capped at `alarm_at + hard_cap_s` regardless.

Tail-close raised from 35s to 60s in Phase 4C: a real Phase 4B live-test
answer had a 37s mid-answer thinking-pause (normal for a longer conceptual
"explain this" question) that tripped the old 35s threshold and silently
truncated the answer to just its first sentence before the substantive
content arrived. 35s was tuned against Phase 2/3's shorter, more clipped
test answers and didn't hold up on a real explain-back question.

The matcher does not attempt topical relevance filtering -- an unrelated
utterance that happens to fall in the window is still included in
raw_text. Deciding whether captured speech is actually an answer (vs.
off-topic chatter) is the grader's job in Phase 5 (the "not_an_answer"
verdict), not the matcher's.
"""

import difflib
from dataclasses import dataclass
from datetime import datetime, timedelta

TRIGGER_PHRASE = "recall answer"
DEFAULT_TAIL_CLOSE_S = 60.0
DEFAULT_HARD_CAP_S = 300.0  # 5 minutes
DEFAULT_FUZZY_THRESHOLD = 0.6


@dataclass(frozen=True)
class Utterance:
    id: int
    text: str
    spoken_at: datetime


@dataclass(frozen=True)
class MatchResult:
    utterances: list[Utterance]
    raw_text: str
    trigger_found: bool  # confidence flag only -- never gates matching


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def fuzzy_contains_trigger(
    text: str, trigger: str = TRIGGER_PHRASE, threshold: float = DEFAULT_FUZZY_THRESHOLD
) -> bool:
    """True if `trigger` appears in `text`, tolerant of STT mangling.

    Exact substring match first; otherwise slides a window the same word
    length as the trigger across the text and checks difflib ratio.
    """
    normalized_text = _normalize(text)
    normalized_trigger = _normalize(trigger)
    if not normalized_text or not normalized_trigger:
        return False
    if normalized_trigger in normalized_text:
        return True

    words = normalized_text.split()
    trigger_len = len(normalized_trigger.split())
    for i in range(len(words) - trigger_len + 1):
        window = " ".join(words[i : i + trigger_len])
        ratio = difflib.SequenceMatcher(None, window, normalized_trigger).ratio()
        if ratio >= threshold:
            return True
    return False


def match_answer_window(
    utterances: list[Utterance],
    alarm_at: datetime,
    tail_close_s: float = DEFAULT_TAIL_CLOSE_S,
    hard_cap_s: float = DEFAULT_HARD_CAP_S,
) -> MatchResult:
    """Select the utterances that belong to a dispatched question's answer.

    Window: opens at alarm_at, hard-capped at alarm_at + hard_cap_s. Within
    that range, keeps including utterances as long as consecutive ones are
    no more than tail_close_s apart; the first gap larger than that closes
    the window (everything after is excluded, even if still under the hard
    cap).
    """
    hard_cap_at = alarm_at + timedelta(seconds=hard_cap_s)
    candidates = sorted(
        (u for u in utterances if alarm_at <= u.spoken_at <= hard_cap_at),
        key=lambda u: u.spoken_at,
    )

    in_window: list[Utterance] = []
    last_time: datetime | None = None
    for u in candidates:
        if last_time is not None and (u.spoken_at - last_time) > timedelta(seconds=tail_close_s):
            break
        in_window.append(u)
        last_time = u.spoken_at

    raw_text = " ".join(u.text for u in in_window)
    trigger_found = any(fuzzy_contains_trigger(u.text) for u in in_window)

    return MatchResult(utterances=in_window, raw_text=raw_text, trigger_found=trigger_found)
