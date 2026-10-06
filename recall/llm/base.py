"""Provider-agnostic LLM interface.

A concrete adapter (e.g. recall/llm/gemini.py) implements this ABC.
Swapping providers should mean a config change plus one new adapter file,
per AGENT.md -- never a rewrite of callers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

GradeVerdict = Literal[
    "correct", "partial", "incorrect", "not_an_answer", "no_answer", "incomplete_capture", "code_truncated"
]
# quality mapping (AGENT.md "Loop policy"): correct->5 (all rubric points) or
# 4 (minor gap), partial->3, incorrect->1, not_an_answer->0, no_answer->0,
# incomplete_capture->no SM-2 update at all (re-dispatch in ~2h instead).
# "no_answer" (empty answer window) and "incomplete_capture" (<12 words
# captured and the LLM's own verdict wasn't already "correct") are both
# handled in code by recall/grading.py -- the LLM only ever returns the
# other four verdicts itself.
# "code_truncated" is never assigned automatically by anything -- it's a
# manual correction tag (Phase 4C) for an answer whose real content was
# silently cut by a matcher/tooling bug (e.g. the pre-Phase-4C 35s tail-close
# truncating a genuine answer) rather than a real grading outcome. Excluded
# from grader-agreement stats in recall/review.py.
SessionType = Literal["lecture_study", "substantive_discussion", "casual", "personal"]


@dataclass(frozen=True)
class GeneratedQuestion:
    text: str
    expected_answer: str
    rubric: list[str]  # 2-4 key points a good explanation must contain
    source_excerpt: str
    topic: str
    language: str


@dataclass(frozen=True)
class QuestionGenerationResult:
    session_type: SessionType
    session_type_reason: str  # one line
    questions: list[GeneratedQuestion]


AnswersWhich = Literal["current", "previous", "neither"]


@dataclass(frozen=True)
class GradeResult:
    verdict: GradeVerdict
    # "not_an_answer" is distinct from "incorrect": off-topic/unrelated
    # speech in the answer window, not a wrong attempt at the question
    # (AGENT.md schema decision, 2026-09-25). Both score as an SM-2 miss.
    rubric_points_hit: list[str]
    quality: int  # SM-2 quality scale, 0-5
    feedback: str  # <=12 words -- fits a wrist notification
    # Phase 5F: which question this speech actually answers, only
    # meaningful when grade_answer was given a previous_question to check
    # against -- "current" (default) when no comparison was requested.
    answers: AnswersWhich = "current"


class LLMProvider(ABC):
    @abstractmethod
    def generate_questions(
        self, transcript: str, n: int, target_language: str | None = None
    ) -> QuestionGenerationResult:
        """Classify the session (lecture_study / substantive_discussion /
        casual / personal, with a one-line reason) and, given a transcript,
        produce up to n retrieval questions with expected answers, grading
        rubrics, and the source excerpt each one came from. Fewer than n
        (including zero) is correct when the transcript doesn't support
        that many distinct, substantive, ungarbled questions.

        target_language: if set, questions are generated in this language
        instead of the transcript's dominant language (Phase 7 groundwork;
        the full language-flag feature isn't built yet)."""
        raise NotImplementedError

    @abstractmethod
    def grade_answer(
        self, question: GeneratedQuestion, spoken_answer: str, previous_question: GeneratedQuestion | None = None
    ) -> GradeResult:
        """Grade a spoken explain-back answer (STT-noisy, possibly
        code-switched) against the question's rubric, expected answer, and
        source excerpt. Never called for an empty answer window -- that's
        handled in code as "no_answer" without an LLM call.

        `previous_question`, if given (Phase 5F, after a live test found
        realtime-transcript lag causing an answer to the PREVIOUS question
        to arrive late and get graded against the current one instead):
        the model also judges whether this speech actually answers
        `previous_question` better than `question` -- returned as
        `GradeResult.answers` ("current" | "previous" | "neither")."""
        raise NotImplementedError

    @abstractmethod
    def shorten_question(self, text: str, max_chars: int) -> str:
        """Rewrite `text` to fit within max_chars characters while
        preserving its meaning. One-shot repair pass for a generated
        question that came back over the length limit (Phase 4C: questions
        must fit a Bee todo notification, <=100 chars)."""
        raise NotImplementedError
