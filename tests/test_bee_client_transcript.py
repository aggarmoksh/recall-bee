"""Phase 5E Step 1: single-source transcript selection in BeeClient.get_transcript.

AGENT.md Loop policy (2026-10-05): while CAPTURING, read ONLY the
realtime transcription; once processed, read the non-realtime one. Never
concatenate -- concatenating both previously double-stored the same
speech under different utterance ids (FRICTION_LOG 2026-10-01/02).
"""

import httpx
import pytest

from recall.bee.client import BeeClient

REALTIME_UTTERANCES = [{"id": 1, "text": "realtime version", "spoken_at": 1000}]
FINAL_UTTERANCES = [{"id": 2, "text": "final version", "spoken_at": 1000}]


def _conversation(state: str, transcriptions: list[dict]) -> dict:
    return {"id": 42, "state": state, "transcriptions": transcriptions}


def _client_for(conversation: dict) -> BeeClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"conversation": conversation})

    client = BeeClient(base_url="http://127.0.0.1:8787")
    client._client = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(handler))
    return client


def test_realtime_only_while_capturing():
    conv = _conversation("CAPTURING", [{"id": 1, "realtime": True, "utterances": REALTIME_UTTERANCES}])
    result = _client_for(conv).get_transcript(42)
    assert result["transcript"] == REALTIME_UTTERANCES
    assert result["state"] == "CAPTURING"


def test_final_only_once_processed():
    conv = _conversation("COMPLETED", [{"id": 2, "realtime": False, "utterances": FINAL_UTTERANCES}])
    result = _client_for(conv).get_transcript(42)
    assert result["transcript"] == FINAL_UTTERANCES
    assert result["state"] == "COMPLETED"


def test_prefers_realtime_over_final_while_still_capturing_even_if_both_present():
    """Real observed shape is one at a time, but the selection logic must
    not accidentally prefer the wrong one if both are ever present."""
    conv = _conversation(
        "CAPTURING",
        [
            {"id": 2, "realtime": False, "utterances": FINAL_UTTERANCES},
            {"id": 1, "realtime": True, "utterances": REALTIME_UTTERANCES},
        ],
    )
    result = _client_for(conv).get_transcript(42)
    assert result["transcript"] == REALTIME_UTTERANCES


def test_prefers_final_over_realtime_once_not_capturing_even_if_both_present():
    conv = _conversation(
        "PROCESSING",
        [
            {"id": 1, "realtime": True, "utterances": REALTIME_UTTERANCES},
            {"id": 2, "realtime": False, "utterances": FINAL_UTTERANCES},
        ],
    )
    result = _client_for(conv).get_transcript(42)
    assert result["transcript"] == FINAL_UTTERANCES


def test_never_concatenates_both_transcriptions():
    conv = _conversation(
        "COMPLETED",
        [
            {"id": 1, "realtime": True, "utterances": REALTIME_UTTERANCES},
            {"id": 2, "realtime": False, "utterances": FINAL_UTTERANCES},
        ],
    )
    result = _client_for(conv).get_transcript(42)
    assert len(result["transcript"]) == 1
    assert result["transcript"] == FINAL_UTTERANCES


def test_falls_back_to_whatever_exists_when_preferred_kind_absent():
    """CAPTURING but no realtime transcription exists yet (mid-switchover) --
    must not return empty, falls back to whatever is there."""
    conv = _conversation("CAPTURING", [{"id": 2, "realtime": False, "utterances": FINAL_UTTERANCES}])
    result = _client_for(conv).get_transcript(42)
    assert result["transcript"] == FINAL_UTTERANCES


def test_no_transcriptions_returns_empty():
    conv = _conversation("CAPTURING", [])
    result = _client_for(conv).get_transcript(42)
    assert result["transcript"] == []
