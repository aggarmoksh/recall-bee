"""Grades a matched answer window and persists the result to Answer.

Two verdicts are handled in code, not by the LLM (AGENT.md "Loop policy"):
- no_answer: an empty answer window (nothing captured) -- skips the LLM call
  entirely, quality 0.
- incomplete_capture: the window has < 12 words and the LLM's own verdict
  wasn't already "correct" -- the LLM is still called (we need its opinion
  to know if a short answer was nonetheless fully correct), but a non-correct
  short verdict gets overridden. No SM-2 update for this verdict (the
  dispatcher re-asks in ~2h instead); quality is a placeholder here since the
  dispatcher branches on verdict, not quality, to decide whether to touch SM-2.
"""

from recall.db import Answer, Dispatch, Question, get_session
from recall.llm import get_provider
from recall.llm.base import GeneratedQuestion, GradeResult
from recall.matcher import MatchResult

MAX_FEEDBACK_WORDS = 12
MIN_WORDS_FOR_CONFIDENT_GRADE = 12
NO_ANSWER_FEEDBACK = "No answer captured."
INCOMPLETE_CAPTURE_FEEDBACK = "Only caught a few words, will ask again"


def _truncate_feedback(feedback: str, max_words: int = MAX_FEEDBACK_WORDS) -> str:
    """Defensive cap in code -- the prompt asks for <=12 words but nothing
    guarantees the model complies exactly, and feedback has to fit a wrist
    notification."""
    words = feedback.split()
    if len(words) <= max_words:
        return feedback
    return " ".join(words[:max_words]) + "..."


def _question_to_generated(question: Question) -> GeneratedQuestion:
    return GeneratedQuestion(
        text=question.text,
        expected_answer=question.expected_answer,
        rubric=question.rubric,
        source_excerpt=question.source_excerpt,
        topic=question.topic or "",
        language=question.language or "",
    )


def grade_match(question: Question, match: MatchResult) -> GradeResult:
    """Pure grading step (no DB writes): no_answer short-circuits without an
    LLM call; otherwise delegates to the configured provider."""
    if not match.raw_text.strip():
        return GradeResult(verdict="no_answer", rubric_points_hit=[], quality=0, feedback=NO_ANSWER_FEEDBACK)

    provider = get_provider()
    result = provider.grade_answer(_question_to_generated(question), match.raw_text)

    word_count = len(match.raw_text.split())
    if word_count < MIN_WORDS_FOR_CONFIDENT_GRADE and result.verdict != "correct":
        return GradeResult(verdict="incomplete_capture", rubric_points_hit=[], quality=0, feedback=INCOMPLETE_CAPTURE_FEEDBACK)

    return GradeResult(
        verdict=result.verdict,
        rubric_points_hit=result.rubric_points_hit,
        quality=result.quality,
        feedback=_truncate_feedback(result.feedback),
    )


def grade_and_persist(dispatch: Dispatch, question: Question, match: MatchResult) -> Answer:
    """Grades and writes the one Answer row for this dispatch
    (Answer.dispatch_id is unique -- AGENT.md schema decision: one graded
    Answer per Dispatch, graded once when the answer window closes)."""
    result = grade_match(question, match)
    db = get_session()
    try:
        answer = Answer(
            dispatch_id=dispatch.id,
            utterance_ids=[u.id for u in match.utterances],
            raw_text=match.raw_text,
            verdict=result.verdict,
            rubric_points_hit=result.rubric_points_hit,
            quality=result.quality,
            feedback=result.feedback,
        )
        db.add(answer)
        db.commit()
        db.refresh(answer)
        return answer
    finally:
        db.close()
