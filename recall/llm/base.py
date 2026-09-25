"""Provider-agnostic LLM interface.

A concrete adapter (e.g. recall/llm/gemini.py) implements this ABC.
Swapping providers should mean a config change plus one new adapter file,
per AGENT.md -- never a rewrite of callers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

GradeVerdict = Literal["correct", "incorrect", "not_an_answer"]


@dataclass(frozen=True)
class GeneratedQuestion:
    text: str
    expected_answer: str
    rubric: list[str]  # 2-4 key points a good explanation must contain
    source_excerpt: str
    topic: str
    language: str


@dataclass(frozen=True)
class GradeResult:
    verdict: GradeVerdict
    # "not_an_answer" is distinct from "incorrect": off-topic/unrelated
    # speech in the answer window, not a wrong attempt at the question
    # (AGENT.md schema decision, 2026-09-25). Both score as an SM-2 miss.
    quality: int  # SM-2 quality scale, 0-5
    feedback: str


class LLMProvider(ABC):
    @abstractmethod
    def generate_questions(
        self, transcript: str, n: int, target_language: str | None = None
    ) -> list[GeneratedQuestion]:
        """Given a transcript, produce n retrieval questions with expected
        answers, grading rubrics, and the source excerpt each one came from.

        target_language: if set, questions are generated in this language
        instead of the transcript's dominant language (Phase 7 groundwork;
        the full language-flag feature isn't built yet)."""
        raise NotImplementedError

    @abstractmethod
    def grade_answer(self, question: GeneratedQuestion, spoken_answer: str) -> GradeResult:
        """Grade a spoken explain-back answer against the question's rubric
        and source excerpt."""
        raise NotImplementedError
