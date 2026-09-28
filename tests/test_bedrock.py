from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from recall.llm.bedrock import BedrockProvider


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr("recall.llm.bedrock.time.sleep", lambda _: None)


@pytest.fixture(autouse=True)
def isolated_log(tmp_path, monkeypatch):
    monkeypatch.setattr("recall.llm.bedrock.LOG_PATH", tmp_path / "llm-usage.ndjson")


def _tool_use_response(input_dict: dict, usage: dict | None = None) -> dict:
    return {
        "output": {"message": {"content": [{"toolUse": {"toolUseId": "t1", "name": "submit", "input": input_dict}}]}},
        "usage": usage or {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
    }


def _provider_with_client(mock_client) -> BedrockProvider:
    provider = BedrockProvider.__new__(BedrockProvider)
    provider.model = "us.amazon.nova-2-lite-v1:0"
    provider._client = mock_client
    return provider


def test_generate_questions_extracts_tool_use_input():
    mock_client = MagicMock()
    mock_client.converse.return_value = _tool_use_response(
        {
            "session_type": "lecture_study",
            "session_type_reason": "structured lecture",
            "questions": [
                {
                    "text": "Why?",
                    "expected_answer": "Because.",
                    "rubric": ["a", "b"],
                    "source_excerpt": "...",
                    "topic": "Topic",
                    "language": "English",
                }
            ],
        }
    )
    provider = _provider_with_client(mock_client)

    result = provider.generate_questions("some transcript", n=1)

    assert result.session_type == "lecture_study"
    assert len(result.questions) == 1
    assert result.questions[0].text == "Why?"
    assert result.questions[0].rubric == ["a", "b"]


def test_generate_questions_uses_temperature_zero():
    mock_client = MagicMock()
    mock_client.converse.return_value = _tool_use_response(
        {"session_type": "casual", "session_type_reason": "small talk", "questions": []}
    )
    provider = _provider_with_client(mock_client)

    provider.generate_questions("chit chat", n=5)

    _, kwargs = mock_client.converse.call_args
    assert kwargs["inferenceConfig"]["temperature"] == 0


def test_grade_answer_extracts_tool_use_input():
    mock_client = MagicMock()
    mock_client.converse.return_value = _tool_use_response(
        {"verdict": "partial", "rubric_points_hit": ["a"], "quality": 3, "feedback": "Missed b."}
    )
    provider = _provider_with_client(mock_client)

    from recall.llm.base import GeneratedQuestion

    q = GeneratedQuestion(text="Q", expected_answer="A", rubric=["a", "b"], source_excerpt="...", topic="t", language="English")
    result = provider.grade_answer(q, "some spoken answer")

    assert result.verdict == "partial"
    assert result.rubric_points_hit == ["a"]
    assert result.quality == 3


def test_falls_back_to_json_text_parsing_when_no_tool_use():
    mock_client = MagicMock()
    mock_client.converse.return_value = {
        "output": {"message": {"content": [{"text": '{"session_type": "casual", "session_type_reason": "r", "questions": []}'}]}},
        "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
    }
    provider = _provider_with_client(mock_client)

    result = provider.generate_questions("transcript", n=1)

    assert result.session_type == "casual"


def test_retries_on_throttling_then_succeeds():
    mock_client = MagicMock()
    throttle_error = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Converse")
    mock_client.converse.side_effect = [
        throttle_error,
        _tool_use_response({"session_type": "casual", "session_type_reason": "r", "questions": []}),
    ]
    provider = _provider_with_client(mock_client)

    result = provider.generate_questions("transcript", n=1)

    assert result.session_type == "casual"
    assert mock_client.converse.call_count == 2


def test_does_not_retry_on_non_retryable_error():
    mock_client = MagicMock()
    mock_client.converse.side_effect = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "bad request"}}, "Converse"
    )
    provider = _provider_with_client(mock_client)

    with pytest.raises(RuntimeError):
        provider.generate_questions("transcript", n=1)

    assert mock_client.converse.call_count == 1


def test_shorten_question_extracts_tool_use_input():
    mock_client = MagicMock()
    mock_client.converse.return_value = _tool_use_response({"text": "Shorter question?"})
    provider = _provider_with_client(mock_client)

    result = provider.shorten_question("A much longer over-length question that needs shortening?", max_chars=30)

    assert result == "Shorter question?"


def test_logs_token_usage(tmp_path, monkeypatch):
    log_path = tmp_path / "usage.ndjson"
    monkeypatch.setattr("recall.llm.bedrock.LOG_PATH", log_path)

    mock_client = MagicMock()
    mock_client.converse.return_value = _tool_use_response(
        {"session_type": "casual", "session_type_reason": "r", "questions": []},
        usage={"inputTokens": 42, "outputTokens": 7, "totalTokens": 49},
    )
    provider = _provider_with_client(mock_client)
    provider.generate_questions("transcript", n=1)

    assert log_path.exists()
    import json

    record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["prompt_tokens"] == 42
    assert record["candidates_tokens"] == 7
    assert record["total_tokens"] == 49
    assert record["model"] == "us.amazon.nova-2-lite-v1:0"
