"""Factory: picks the LLM provider from config (LLM_PROVIDER).

Swapping providers is a config change plus one new adapter file (AGENT.md) --
callers only ever import get_provider(), never a concrete adapter class.
"""

from recall.config import settings
from recall.llm.base import GeneratedQuestion, GradeResult, GradeVerdict, LLMProvider


def get_provider() -> LLMProvider:
    if settings.llm_provider == "gemini":
        from recall.llm.gemini import GeminiProvider

        return GeminiProvider(api_key=settings.gemini_api_key, model=settings.llm_model)
    raise ValueError(f"unknown LLM_PROVIDER: {settings.llm_provider!r} (supported: 'gemini')")


__all__ = ["get_provider", "LLMProvider", "GeneratedQuestion", "GradeResult", "GradeVerdict"]
