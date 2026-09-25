from datetime import UTC, datetime, timedelta

from recall.db import Utterance
from recall.questions import locate_source_window

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _u(id_: int, text: str, offset_s: float) -> Utterance:
    return Utterance(id=id_, conversation_id=1, conversation_uuid="x", text=text, spoken_at=BASE + timedelta(seconds=offset_s))


def test_exact_substring_match_single_utterance():
    utterances = [
        _u(1, "Before that it was the Roman Kingdom.", 0),
        _u(2, "Then came the Republic.", 10),
    ]
    start, end = locate_source_window(utterances, "the Roman Kingdom")
    assert start == utterances[0].spoken_at
    assert end == utterances[0].spoken_at


def test_exact_substring_spanning_multiple_utterances_uses_first_and_last_match():
    utterances = [
        _u(1, "the Roman Kingdom was first", 0),
        _u(2, "unrelated aside", 10),
        _u(3, "the Roman Kingdom fell eventually", 20),
    ]
    start, end = locate_source_window(utterances, "the Roman Kingdom")
    assert start == utterances[0].spoken_at
    assert end == utterances[2].spoken_at


def test_fuzzy_fallback_when_no_exact_match():
    utterances = [_u(1, "Before the Roman Empire there was the Roman Republic", 0)]
    # slightly mangled quote (STT-style) that doesn't appear verbatim
    start, end = locate_source_window(utterances, "before the roman empire there was the roman republc")
    assert start == utterances[0].spoken_at
    assert end == utterances[0].spoken_at


def test_no_match_returns_none_none():
    utterances = [_u(1, "completely unrelated content", 0)]
    start, end = locate_source_window(utterances, "something totally different about spacecraft")
    assert start is None
    assert end is None


def test_empty_excerpt_returns_none_none():
    utterances = [_u(1, "some text", 0)]
    start, end = locate_source_window(utterances, "")
    assert start is None
    assert end is None
