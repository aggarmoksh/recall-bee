"""Prototype answer matcher.

For a dispatched question's answer window, collect the utterances that fall
inside it, fuzzy-detect the "Recall answer" trigger phrase (STT mangles odd
short phrases -- AGENT.md pitfall), and join the text into one raw answer.

The gate script (phase2/explain_gate.py) uses Enter-press timestamps for the
window; production will use alarm_at-derived windows instead, per the brief.
"""

import difflib
from dataclasses import dataclass
from datetime import datetime, timedelta

TRIGGER_PHRASE = "recall answer"
DEFAULT_WINDOW_PAD_S = 15
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
    trigger_found: bool


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
    speech_start: datetime,
    speech_end: datetime,
    pad_seconds: float = DEFAULT_WINDOW_PAD_S,
) -> MatchResult:
    """Select utterances within [speech_start - pad, speech_end + pad],
    join their text in spoken_at order, and check for the trigger phrase."""
    window_start = speech_start - timedelta(seconds=pad_seconds)
    window_end = speech_end + timedelta(seconds=pad_seconds)

    in_window = sorted(
        (u for u in utterances if window_start <= u.spoken_at <= window_end),
        key=lambda u: u.spoken_at,
    )
    raw_text = " ".join(u.text for u in in_window)
    trigger_found = any(fuzzy_contains_trigger(u.text) for u in in_window)

    return MatchResult(utterances=in_window, raw_text=raw_text, trigger_found=trigger_found)
