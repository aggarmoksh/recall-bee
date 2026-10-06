"""Bedrock adapter (default LLM provider per AGENT.md, revised 2026-09-27).

Uses the Converse API with tool-use (function calling) as the structured-
output mechanism: Nova 2 Lite's model card explicitly lists "Structured
outputs" as NOT supported, but "Client-side tool calling" IS supported, so
forcing a single tool call with a JSON-schema input is the standard
workaround. Falls back to strict JSON parsing of a text response (with one
repair retry) only if the model doesn't return a tool call.

Temperature 0 for both jobs (classification is part of generate_questions;
grading is grade_answer), per AGENT.md's "Loop policy" rule.

Token usage logged to logs/llm-usage.ndjson exactly like the Gemini adapter.
"""

import json
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from recall.llm.base import (
    GeneratedQuestion,
    GradeResult,
    GradeVerdict,
    LLMProvider,
    QuestionGenerationResult,
    SessionType,
)

LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "llm-usage.ndjson"

MAX_RETRIES = 4
BACKOFF_BASE_S = 2.0
RETRYABLE_ERROR_CODES = {
    "ThrottlingException",
    "ServiceUnavailableException",
    "ModelTimeoutException",
    "InternalServerException",
}

_QUESTION_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "session_type": {
            "type": "string",
            "enum": ["lecture_study", "substantive_discussion", "casual", "personal"],
        },
        "session_type_reason": {"type": "string"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "expected_answer": {"type": "string"},
                    "rubric": {"type": "array", "items": {"type": "string"}},
                    "source_excerpt": {"type": "string"},
                    "topic": {"type": "string"},
                    "language": {"type": "string"},
                },
                "required": ["text", "expected_answer", "rubric", "source_excerpt", "topic", "language"],
            },
        },
    },
    "required": ["session_type", "session_type_reason", "questions"],
}

_GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["correct", "partial", "incorrect", "not_an_answer"]},
        "rubric_points_hit": {"type": "array", "items": {"type": "string"}},
        "quality": {"type": "integer"},
        "feedback": {"type": "string"},
    },
    "required": ["verdict", "rubric_points_hit", "quality", "feedback"],
}

_GRADE_SCHEMA_WITH_PREVIOUS = {
    "type": "object",
    "properties": {
        **_GRADE_SCHEMA["properties"],
        "answers": {"type": "string", "enum": ["current", "previous", "neither"]},
    },
    "required": [*_GRADE_SCHEMA["required"], "answers"],
}

_SHORTEN_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def _log_usage(job: str, model: str, usage: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": time.time(),
        "job": job,
        "model": model,
        "prompt_tokens": usage.get("inputTokens"),
        "candidates_tokens": usage.get("outputTokens"),
        "total_tokens": usage.get("totalTokens"),
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


class BedrockProvider(LLMProvider):
    def __init__(self, region: str, model: str):
        self.model = model
        self._client = boto3.client("bedrock-runtime", region_name=region)

    def _converse_structured(self, job: str, prompt: str, tool_name: str, schema: dict) -> dict:
        tool_config = {
            "tools": [
                {
                    "toolSpec": {
                        "name": tool_name,
                        "description": f"Submit the {tool_name} result as structured JSON matching the schema.",
                        "inputSchema": {"json": schema},
                    }
                }
            ],
            "toolChoice": {"tool": {"name": tool_name}},
        }
        current_prompt = prompt
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                response = self._client.converse(
                    modelId=self.model,
                    messages=[{"role": "user", "content": [{"text": current_prompt}]}],
                    inferenceConfig={"temperature": 0},
                    toolConfig=tool_config,
                )
            except ClientError as e:
                last_error = e
                code = e.response.get("Error", {}).get("Code", "")
                if code in RETRYABLE_ERROR_CODES and attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF_BASE_S * (2**attempt))
                    continue
                raise RuntimeError(f"Bedrock API call failed ({job}, model={self.model}, code={code}): {e}") from e

            usage = response.get("usage") or {}
            _log_usage(job, self.model, usage)

            content_blocks = response.get("output", {}).get("message", {}).get("content", [])
            for block in content_blocks:
                if "toolUse" in block:
                    return block["toolUse"]["input"]

            # Fallback: no tool call -- try strict JSON parsing of any text block,
            # with one repair retry asking the model to call the tool properly.
            text = next((b["text"] for b in content_blocks if "text" in b), "")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                if attempt < MAX_RETRIES - 1:
                    current_prompt = (
                        prompt
                        + "\n\nYour previous response did not call the tool with valid structured output. "
                        f"Call the `{tool_name}` tool now with JSON matching the schema exactly."
                    )
                    continue
                raise RuntimeError(f"Bedrock returned no tool call and unparseable text for {job} (model={self.model})")

        raise RuntimeError(f"Bedrock API call failed after {MAX_RETRIES} attempts ({job}): {last_error}")

    def generate_questions(
        self, transcript: str, n: int, target_language: str | None = None
    ) -> QuestionGenerationResult:
        from recall.prompts import render_generate_questions_prompt

        prompt = render_generate_questions_prompt(transcript=transcript, n=n, target_language=target_language)
        result = self._converse_structured("generate_questions", prompt, "submit_questions", _QUESTION_LIST_SCHEMA)
        session_type: SessionType = result["session_type"]
        return QuestionGenerationResult(
            session_type=session_type,
            session_type_reason=result["session_type_reason"],
            questions=[
                GeneratedQuestion(
                    text=q["text"],
                    expected_answer=q["expected_answer"],
                    rubric=q["rubric"],
                    source_excerpt=q["source_excerpt"],
                    topic=q["topic"],
                    language=q["language"],
                )
                for q in result["questions"]
            ],
        )

    def grade_answer(
        self, question: GeneratedQuestion, spoken_answer: str, previous_question: GeneratedQuestion | None = None
    ) -> GradeResult:
        from recall.prompts import render_grade_answer_prompt

        prompt = render_grade_answer_prompt(
            question=question.text,
            expected_answer=question.expected_answer,
            rubric=question.rubric,
            source_excerpt=question.source_excerpt,
            answer_text=spoken_answer,
            previous_question=previous_question.text if previous_question else None,
        )
        schema = _GRADE_SCHEMA_WITH_PREVIOUS if previous_question is not None else _GRADE_SCHEMA
        result = self._converse_structured("grade_answer", prompt, "submit_grade", schema)
        verdict: GradeVerdict = result["verdict"]
        return GradeResult(
            verdict=verdict,
            rubric_points_hit=result["rubric_points_hit"],
            quality=result["quality"],
            feedback=result["feedback"],
            answers=result.get("answers", "current"),
        )

    def shorten_question(self, text: str, max_chars: int) -> str:
        from recall.prompts import render_shorten_question_prompt

        prompt = render_shorten_question_prompt(text=text, max_chars=max_chars)
        result = self._converse_structured("shorten_question", prompt, "submit_shortened_text", _SHORTEN_SCHEMA)
        return result["text"]
