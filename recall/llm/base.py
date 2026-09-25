"""Provider-agnostic LLM interface. No implementation yet (Phase 3/5).

A concrete adapter (e.g. recall/llm/anthropic_provider.py) implements this
ABC. Swapping providers should mean a config change plus one new adapter
file, per AGENT.md -- never a rewrite of callers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class GeneratedQuestion:
    text: str
    expected_answer: str
    source_excerpt: str


@dataclass(frozen=True)
class GradeResult:
    correct: bool
    quality: int  # SM-2 quality scale, 0-5
    feedback: str


class LLMProvider(ABC):
    @abstractmethod
    def generate_questions(self, transcript: str, n: int) -> list[GeneratedQuestion]:
        """Given a transcript, produce n retrieval questions with expected answers
        and the source excerpt each one came from."""
        raise NotImplementedError

    @abstractmethod
    def grade_answer(self, question: GeneratedQuestion, spoken_answer: str) -> GradeResult:
        """Grade a spoken explain-back answer against the question's expected
        answer and source excerpt."""
        raise NotImplementedError
