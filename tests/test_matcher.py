from datetime import UTC, datetime, timedelta

from recall.matcher import Utterance, fuzzy_contains_trigger, match_answer_window


def _dt(offset_s: float, base: datetime | None = None) -> datetime:
    base = base or datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    return base + timedelta(seconds=offset_s)


def test_fuzzy_exact_match():
    assert fuzzy_contains_trigger("Recall answer, Paris is the capital.")


def test_fuzzy_case_and_whitespace_insensitive():
    assert fuzzy_contains_trigger("  RECALL   ANSWER   the sky is blue  ")


def test_fuzzy_mangled_stt_phrase():
    # Similar in shape to the real Phase 0.5 mangling: "Recall check four
    # second sample" -> "Recall check, recall check for second sample."
    assert fuzzy_contains_trigger("Recall, recall answers the capital is Paris")


def test_fuzzy_no_match_on_unrelated_text():
    assert not fuzzy_contains_trigger("The weather today is quite nice.")


def test_fuzzy_empty_text():
    assert not fuzzy_contains_trigger("")


def test_match_window_includes_utterances_in_range():
    start = _dt(0)
    end = _dt(50)
    utterances = [
        Utterance(id=1, text="Recall answer, the mitochondria", spoken_at=_dt(5)),
        Utterance(id=2, text="is the powerhouse of the cell.", spoken_at=_dt(30)),
    ]
    result = match_answer_window(utterances, speech_start=start, speech_end=end)
    assert len(result.utterances) == 2
    assert result.trigger_found is True
    assert "mitochondria" in result.raw_text
    assert "powerhouse" in result.raw_text


def test_match_window_excludes_utterances_outside_range():
    start = _dt(100)
    end = _dt(150)
    utterances = [
        Utterance(id=1, text="Unrelated earlier chatter", spoken_at=_dt(0)),
        Utterance(id=2, text="Recall answer, Paris", spoken_at=_dt(120)),
        Utterance(id=3, text="Unrelated later chatter", spoken_at=_dt(300)),
    ]
    result = match_answer_window(utterances, speech_start=start, speech_end=end)
    assert len(result.utterances) == 1
    assert result.utterances[0].id == 2


def test_match_window_respects_pad_seconds():
    start = _dt(100)
    end = _dt(150)
    # 10s before the padded window (pad=15s -> window opens at 85s)
    utterances = [Utterance(id=1, text="Recall answer, early", spoken_at=_dt(80))]
    result = match_answer_window(utterances, speech_start=start, speech_end=end, pad_seconds=15)
    assert len(result.utterances) == 0
    assert result.trigger_found is False

    result_wider_pad = match_answer_window(utterances, speech_start=start, speech_end=end, pad_seconds=25)
    assert len(result_wider_pad.utterances) == 1


def test_match_window_orders_utterances_by_spoken_at():
    start = _dt(0)
    end = _dt(50)
    utterances = [
        Utterance(id=2, text="second", spoken_at=_dt(30)),
        Utterance(id=1, text="first", spoken_at=_dt(5)),
    ]
    result = match_answer_window(utterances, speech_start=start, speech_end=end)
    assert [u.id for u in result.utterances] == [1, 2]
    assert result.raw_text == "first second"


def test_match_window_no_utterances_returns_empty_result():
    result = match_answer_window([], speech_start=_dt(0), speech_end=_dt(60))
    assert result.utterances == []
    assert result.raw_text == ""
    assert result.trigger_found is False
