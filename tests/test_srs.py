import pytest

from recall.srs import MIN_EASE_FACTOR, sm2, update_ease_factor


def test_first_review_pass_sets_interval_1():
    result = sm2(quality=5, ease_factor=2.5, interval_days=0, repetitions=0)
    assert result.repetitions == 1
    assert result.interval_days == 1


def test_second_review_pass_sets_interval_6():
    result = sm2(quality=4, ease_factor=2.5, interval_days=1, repetitions=1)
    assert result.repetitions == 2
    assert result.interval_days == 6


def test_third_review_uses_ease_factor_multiplier():
    result = sm2(quality=5, ease_factor=2.5, interval_days=6, repetitions=2)
    assert result.repetitions == 3
    assert result.interval_days == round(6 * result.ease_factor)


def test_lapse_resets_repetitions_and_interval():
    result = sm2(quality=2, ease_factor=2.5, interval_days=30, repetitions=5)
    assert result.repetitions == 0
    assert result.interval_days == 1


def test_lapse_still_updates_ease_factor():
    result = sm2(quality=0, ease_factor=2.5, interval_days=30, repetitions=5)
    assert result.ease_factor < 2.5


@pytest.mark.parametrize("quality", [0, 1, 2, 3, 4, 5])
def test_all_quality_values_accepted(quality):
    result = sm2(quality=quality, ease_factor=2.5, interval_days=1, repetitions=0)
    assert result.ease_factor >= MIN_EASE_FACTOR


@pytest.mark.parametrize("quality", [-1, 6, 10])
def test_invalid_quality_raises(quality):
    with pytest.raises(ValueError):
        sm2(quality=quality, ease_factor=2.5, interval_days=1, repetitions=0)


def test_ease_factor_floor_never_breached():
    ef = 1.3
    for _ in range(20):
        ef = update_ease_factor(ef, quality=0)
    assert ef == MIN_EASE_FACTOR


def test_ease_factor_floor_from_high_start_with_repeated_lapses():
    ef = 2.5
    for _ in range(50):
        ef = update_ease_factor(ef, quality=0)
    assert ef >= MIN_EASE_FACTOR
    assert ef == pytest.approx(MIN_EASE_FACTOR)
