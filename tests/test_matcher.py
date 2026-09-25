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
    assert fuzzy_contains_trigger("Recall, recall answers the capital is Paris")


def test_fuzzy_no_match_on_unrelated_text():
    assert not fuzzy_contains_trigger("The weather today is quite nice.")


def test_fuzzy_empty_text():
    assert not fuzzy_contains_trigger("")


def test_match_includes_utterances_starting_at_alarm_at():
    alarm_at = _dt(0)
    utterances = [
        Utterance(id=1, text="Recall answer, the mitochondria", spoken_at=_dt(5)),
        Utterance(id=2, text="is the powerhouse of the cell.", spoken_at=_dt(30)),
    ]
    result = match_answer_window(utterances, alarm_at=alarm_at)
    assert len(result.utterances) == 2
    assert result.trigger_found is True
    assert "mitochondria" in result.raw_text
    assert "powerhouse" in result.raw_text


def test_match_excludes_utterances_before_alarm_at():
    alarm_at = _dt(100)
    utterances = [
        Utterance(id=1, text="earlier unrelated chatter", spoken_at=_dt(50)),
        Utterance(id=2, text="the actual answer", spoken_at=_dt(105)),
    ]
    result = match_answer_window(utterances, alarm_at=alarm_at)
    assert [u.id for u in result.utterances] == [2]


def test_match_with_no_trigger_phrase_still_matches():
    """Trigger phrase is now a confidence flag only -- absence must not
    prevent matching (Phase 2 gate: operator forgot it 3/10 times)."""
    alarm_at = _dt(0)
    utterances = [
        Utterance(id=1, text="The Romans kidnapped the Sabine women because", spoken_at=_dt(3)),
        Utterance(id=2, text="they needed more people to grow the city.", spoken_at=_dt(20)),
    ]
    result = match_answer_window(utterances, alarm_at=alarm_at)
    assert len(result.utterances) == 2
    assert result.trigger_found is False
    assert "Sabine" in result.raw_text


def test_window_closes_35s_after_last_utterance_by_default():
    alarm_at = _dt(0)
    utterances = [
        Utterance(id=1, text="first part of the answer", spoken_at=_dt(5)),
        # gap of 40s from the previous utterance -- exceeds the 35s tail-close
        Utterance(id=2, text="unrelated speech long after", spoken_at=_dt(45)),
    ]
    result = match_answer_window(utterances, alarm_at=alarm_at)
    assert [u.id for u in result.utterances] == [1]
    assert "unrelated speech long after" not in result.raw_text


def test_window_stays_open_across_gaps_under_35s():
    alarm_at = _dt(0)
    utterances = [
        Utterance(id=1, text="first part", spoken_at=_dt(5)),
        Utterance(id=2, text="second part after a pause", spoken_at=_dt(38)),  # 33s gap, under 35s
    ]
    result = match_answer_window(utterances, alarm_at=alarm_at)
    assert len(result.utterances) == 2


def test_no_tail_close_constraint_before_first_utterance():
    """The wearer may take a while to notice the prompt and start
    answering -- only gaps BETWEEN utterances are subject to the 35s
    tail-close; the wait for the first utterance is not."""
    alarm_at = _dt(0)
    utterances = [Utterance(id=1, text="a delayed answer", spoken_at=_dt(120))]  # 2 min after alarm_at
    result = match_answer_window(utterances, alarm_at=alarm_at)
    assert len(result.utterances) == 1


def test_hard_cap_excludes_utterances_past_5_minutes():
    alarm_at = _dt(0)
    utterances = [Utterance(id=1, text="way too late", spoken_at=_dt(301))]  # just past the 300s hard cap
    result = match_answer_window(utterances, alarm_at=alarm_at)
    assert result.utterances == []


def test_hard_cap_is_inclusive_at_exactly_5_minutes():
    alarm_at = _dt(0)
    utterances = [Utterance(id=1, text="right at the cap", spoken_at=_dt(300))]
    result = match_answer_window(utterances, alarm_at=alarm_at)
    assert len(result.utterances) == 1


def test_unrelated_speech_within_window_is_included_not_filtered():
    """The matcher does topical NO filtering -- relevance is the grader's
    job (the Phase 5 not_an_answer verdict), not the matcher's."""
    alarm_at = _dt(0)
    utterances = [
        Utterance(id=1, text="did you take out the trash yet", spoken_at=_dt(5)),
        Utterance(id=2, text="also we need more milk", spoken_at=_dt(15)),
    ]
    result = match_answer_window(utterances, alarm_at=alarm_at)
    assert len(result.utterances) == 2
    assert result.trigger_found is False
    assert "trash" in result.raw_text
    assert "milk" in result.raw_text


def test_match_window_orders_utterances_by_spoken_at():
    alarm_at = _dt(0)
    utterances = [
        Utterance(id=2, text="second", spoken_at=_dt(30)),
        Utterance(id=1, text="first", spoken_at=_dt(5)),
    ]
    result = match_answer_window(utterances, alarm_at=alarm_at)
    assert [u.id for u in result.utterances] == [1, 2]
    assert result.raw_text == "first second"


def test_no_utterances_returns_empty_result():
    result = match_answer_window([], alarm_at=_dt(0))
    assert result.utterances == []
    assert result.raw_text == ""
    assert result.trigger_found is False


def test_custom_tail_close_and_hard_cap_params():
    alarm_at = _dt(0)
    utterances = [
        Utterance(id=1, text="first", spoken_at=_dt(5)),
        Utterance(id=2, text="second, 12s later", spoken_at=_dt(17)),
    ]
    result = match_answer_window(utterances, alarm_at=alarm_at, tail_close_s=10, hard_cap_s=60)
    # 12s gap exceeds a tightened 10s tail-close
    assert [u.id for u in result.utterances] == [1]
