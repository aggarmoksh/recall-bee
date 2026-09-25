"""SM-2 spaced-repetition algorithm, hand-implemented, pure functions only."""

from dataclasses import dataclass

MIN_EASE_FACTOR = 1.3
DEFAULT_EASE_FACTOR = 2.5


@dataclass(frozen=True)
class ReviewResult:
    ease_factor: float
    interval_days: int
    repetitions: int


def update_ease_factor(ease_factor: float, quality: int) -> float:
    """Standard SM-2 EF update, floored at 1.3. Applied regardless of pass/fail."""
    new_ef = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    return max(MIN_EASE_FACTOR, new_ef)


def sm2(quality: int, ease_factor: float, interval_days: int, repetitions: int) -> ReviewResult:
    """One SM-2 review step.

    quality: 0-5 (< 3 counts as a lapse -- resets repetitions and restarts
    the interval schedule at 1 day, but EF still updates on the same review).
    """
    if not 0 <= quality <= 5:
        raise ValueError(f"quality must be 0-5, got {quality}")

    new_ef = update_ease_factor(ease_factor, quality)

    if quality < 3:
        return ReviewResult(ease_factor=new_ef, interval_days=1, repetitions=0)

    new_repetitions = repetitions + 1
    if new_repetitions == 1:
        new_interval = 1
    elif new_repetitions == 2:
        new_interval = 6
    else:
        new_interval = round(interval_days * new_ef)

    return ReviewResult(ease_factor=new_ef, interval_days=new_interval, repetitions=new_repetitions)
