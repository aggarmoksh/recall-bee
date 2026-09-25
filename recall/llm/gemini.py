"""Gemini adapter (default LLM provider, per AGENT.md).

Uses the google-genai SDK with structured JSON output (a pydantic
response_schema) so responses parse deterministically -- no free-text
parsing. Every call's token counts are logged to logs/llm-usage.ndjson for
cost tracking. Rate-limit/quota errors are retried with backoff; a clear
error is raised once retries are exhausted.
"""

import json
import time
from pathlib import Path
from typing import Literal

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from recall.llm.base import GeneratedQuestion, GradeResult, GradeVerdict, LLMProvider

LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "llm-usage.ndjson"

MAX_RETRIES = 4
BACKOFF_BASE_S = 2.0
# Gemini uses HTTP-style status codes on APIError; 429 = rate limit/quota
# exhausted, 503 = model overloaded -- both are worth retrying with backoff.
RETRYABLE_STATUSES = {429, 503}


class _QuestionSchema(BaseModel):
    text: str
    expected_answer: str
    rubric: list[str]
    source_excerpt: str
    topic: str
    language: str


class _QuestionListSchema(BaseModel):
    questions: list[_QuestionSchema]


class _GradeSchema(BaseModel):
    verdict: Literal["correct", "incorrect", "not_an_answer"]
    quality: int
    feedback: str


def _log_usage(job: str, model: str, usage) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": time.time(),
        "job": job,
        "model": model,
        "prompt_tokens": getattr(usage, "prompt_token_count", None),
        "candidates_tokens": getattr(usage, "candidates_token_count", None),
        "total_tokens": getattr(usage, "total_token_count", None),
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set -- required for the Gemini LLM provider")
        self.model = model
        self._client = genai.Client(api_key=api_key)

    def _generate(self, job: str, prompt: str, response_schema: type[BaseModel]) -> BaseModel:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=response_schema,
                    ),
                )
            except errors.APIError as e:
                last_error = e
                code = getattr(e, "code", None)  # int, e.g. 503 -- .status is the string name, e.g. "UNAVAILABLE"
                if code in RETRYABLE_STATUSES and attempt < MAX_RETRIES - 1:
                    wait = BACKOFF_BASE_S * (2**attempt)
                    time.sleep(wait)
                    continue
                raise RuntimeError(
                    f"Gemini API call failed ({job}, model={self.model}, code={code}, status={getattr(e, 'status', None)}): {e}. "
                    "If this is a rate-limit/quota error, wait and retry, or check your plan's quota "
                    "at https://ai.google.dev/gemini-api/docs/rate-limits."
                ) from e

            if response.usage_metadata is not None:
                _log_usage(job, self.model, response.usage_metadata)

            if response.parsed is None:
                raise RuntimeError(f"Gemini returned no parseable structured output for {job} (model={self.model})")
            return response.parsed

        raise RuntimeError(f"Gemini API call failed after {MAX_RETRIES} attempts ({job}): {last_error}")

    def generate_questions(
        self, transcript: str, n: int, target_language: str | None = None
    ) -> list[GeneratedQuestion]:
        from recall.prompts import render_generate_questions_prompt

        prompt = render_generate_questions_prompt(transcript=transcript, n=n, target_language=target_language)
        result = self._generate("generate_questions", prompt, _QuestionListSchema)
        return [
            GeneratedQuestion(
                text=q.text,
                expected_answer=q.expected_answer,
                rubric=q.rubric,
                source_excerpt=q.source_excerpt,
                topic=q.topic,
                language=q.language,
            )
            for q in result.questions
        ]

    def grade_answer(self, question: GeneratedQuestion, spoken_answer: str) -> GradeResult:
        prompt = (
            "You are grading a spoken explanation against a rubric. "
            "Return verdict \"not_an_answer\" if the speech is off-topic or unrelated to the question "
            "(not merely wrong) -- e.g. small talk or a different subject caught in the answer window.\n\n"
            f"Question: {question.text}\n"
            f"Expected answer: {question.expected_answer}\n"
            f"Rubric (key points a good explanation should contain): {question.rubric}\n"
            f"Source excerpt: {question.source_excerpt}\n\n"
            f"Spoken answer (transcribed, may contain STT errors): {spoken_answer}\n\n"
            "Grade quality on the SM-2 scale 0-5 (0 = no answer/totally wrong, 5 = perfect). "
            "A not_an_answer verdict should have quality 0."
        )
        result = self._generate("grade_answer", prompt, _GradeSchema)
        verdict: GradeVerdict = result.verdict
        return GradeResult(verdict=verdict, quality=result.quality, feedback=result.feedback)
