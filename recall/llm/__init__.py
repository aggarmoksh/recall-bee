"""Factory: picks the LLM provider from config (LLM_PROVIDER).

Swapping providers is a config change plus one new adapter file (AGENT.md) --
callers only ever import get_provider(), never a concrete adapter class.

Every provider returned here is wrapped with a hard daily call cap (80/day,
AGENT.md Loop policy), enforced against a DB-persisted counter so it holds
across process restarts.
"""

import logging
from datetime import UTC, datetime

from sqlmodel import select

from recall.config import settings
from recall.db import LLMCallLog, get_session
from recall.dispatch import to_local
from recall.llm.base import (
    GeneratedQuestion,
    GradeResult,
    GradeVerdict,
    LLMProvider,
    QuestionGenerationResult,
)

logger = logging.getLogger("recall.llm")

DAILY_CALL_CAP = 80


class DailyCallCapExceeded(RuntimeError):
    pass


def _today_local() -> str:
    """'Today' is the local calendar day, same convention as the rest of the
    Loop policy's daily caps (recall.dispatch's 6/day cap, quiet hours) --
    not UTC, and not the OS's implicit local timezone."""
    return to_local(datetime.now(UTC)).date().isoformat()


def _check_and_increment_daily_calls() -> None:
    today = _today_local()
    db = get_session()
    try:
        row = db.exec(select(LLMCallLog).where(LLMCallLog.date == today)).first()
        if row is None:
            row = LLMCallLog(date=today, count=0)
        if row.count >= DAILY_CALL_CAP:
            logger.warning("LLM daily call cap (%d) reached for %s -- refusing call", DAILY_CALL_CAP, today)
            raise DailyCallCapExceeded(f"Daily LLM call cap ({DAILY_CALL_CAP}) reached for {today}.")
        row.count += 1
        db.add(row)
        db.commit()
    finally:
        db.close()


def calls_used_today() -> int:
    today = _today_local()
    db = get_session()
    try:
        row = db.exec(select(LLMCallLog).where(LLMCallLog.date == today)).first()
        return row.count if row else 0
    finally:
        db.close()


class _QuotaEnforcedProvider(LLMProvider):
    """Wraps a real provider, checking+incrementing the daily call counter
    before every call so the cap applies uniformly regardless of provider."""

    def __init__(self, inner: LLMProvider):
        self._inner = inner

    def generate_questions(self, transcript: str, n: int, target_language: str | None = None) -> QuestionGenerationResult:
        _check_and_increment_daily_calls()
        return self._inner.generate_questions(transcript, n, target_language)

    def grade_answer(
        self, question: GeneratedQuestion, spoken_answer: str, previous_question: GeneratedQuestion | None = None
    ) -> GradeResult:
        _check_and_increment_daily_calls()
        return self._inner.grade_answer(question, spoken_answer, previous_question=previous_question)

    def shorten_question(self, text: str, max_chars: int) -> str:
        _check_and_increment_daily_calls()
        return self._inner.shorten_question(text, max_chars)


def _build_provider() -> LLMProvider:
    if settings.llm_provider == "gemini":
        from recall.llm.gemini import GeminiProvider

        return GeminiProvider(api_key=settings.gemini_api_key, model=settings.llm_model)
    if settings.llm_provider == "bedrock":
        from recall.llm.bedrock import BedrockProvider

        return BedrockProvider(region=settings.aws_region, model=settings.llm_model)
    raise ValueError(f"unknown LLM_PROVIDER: {settings.llm_provider!r} (supported: 'gemini', 'bedrock')")


def get_provider() -> LLMProvider:
    return _QuotaEnforcedProvider(_build_provider())


__all__ = [
    "get_provider",
    "calls_used_today",
    "DailyCallCapExceeded",
    "DAILY_CALL_CAP",
    "LLMProvider",
    "GeneratedQuestion",
    "GradeResult",
    "GradeVerdict",
]
