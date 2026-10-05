"""httpx client for the bee proxy REST surface.

Every call is retried with backoff and logged raw (request + response) to
logs/bee-events.ndjson, per AGENT.md's quality bar.
"""

import json
import time
from pathlib import Path
from typing import Any

import httpx

LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "bee-events.ndjson"
JSON_CHARSET_HEADER = {"Content-Type": "application/json; charset=utf-8"}

MAX_RETRIES = 3
BACKOFF_BASE_S = 0.5
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _log_event(method: str, url: str, status: int | None, request_body: Any, response_body: Any, error: str | None = None) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": time.time(),
        "method": method,
        "url": url,
        "status": status,
        "request_body": request_body,
        "response_body": response_body,
        "error": error,
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


class BeeClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BeeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _request(self, method: str, path: str, json_body: dict | None = None, params: dict | None = None) -> dict:
        headers = JSON_CHARSET_HEADER if json_body is not None else None
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self._client.request(method, path, json=json_body, params=params, headers=headers)
            except httpx.HTTPError as e:
                last_error = str(e)
                _log_event(method, path, None, json_body, None, error=last_error)
                time.sleep(BACKOFF_BASE_S * (2**attempt))
                continue

            body = None
            try:
                body = resp.json()
            except ValueError:
                body = resp.text

            _log_event(method, path, resp.status_code, json_body, body)

            if resp.status_code in RETRYABLE_STATUS and attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_BASE_S * (2**attempt))
                continue

            resp.raise_for_status()
            return body

        raise RuntimeError(f"{method} {path} failed after {MAX_RETRIES} attempts: {last_error}")

    def get_me(self) -> dict:
        return self._request("GET", "/v1/me")

    def list_conversations(self, limit: int = 20, cursor: str | None = None) -> dict:
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/v1/conversations", params=params)

    def get_transcript(self, conversation_id: int, since: int | None = None) -> dict:
        """Flattened {conversationId, transcript: [utterances], state},
        matching the `bee conversations transcript` CLI output shape plus
        the conversation's own state for callers that need it.

        There is no separate verified `/transcript` REST route (probed and got
        404 on every plausible path). This reuses the confirmed-working
        GET /v1/conversations/{id}, which embeds utterances nested under
        `transcriptions[].utterances`.

        Single-source selection (AGENT.md Loop policy, Phase 5D 2026-10-05):
        while the conversation is CAPTURING, read ONLY the `realtime: true`
        transcription; once it has moved on, read the non-realtime (final)
        one. Falls back to whatever transcription exists if the preferred
        kind is absent (e.g. mid-switchover). NEVER concatenates multiple
        transcriptions -- doing so previously stored the same speech twice
        under different utterance ids (see FRICTION_LOG 2026-10-01/02).
        `since` filters by `spoken_at` (ms epoch) client-side, since the
        endpoint doesn't take it as a query param.
        """
        data = self._request("GET", f"/v1/conversations/{conversation_id}")
        conversation = data.get("conversation", data)
        state = conversation.get("state")
        transcriptions = conversation.get("transcriptions") or []

        utterances = []
        if transcriptions:
            prefer_realtime = state == "CAPTURING"
            chosen = next((t for t in transcriptions if bool(t.get("realtime")) == prefer_realtime), None)
            if chosen is None:
                chosen = transcriptions[0]
            utterances = list(chosen.get("utterances") or [])

        if since is not None:
            utterances = [u for u in utterances if u.get("spoken_at", 0) >= since]
        utterances.sort(key=lambda u: u.get("spoken_at", 0))
        return {"conversationId": conversation_id, "transcript": utterances, "state": state}

    def list_todos(self, limit: int = 20, cursor: str | None = None) -> dict:
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/v1/todos", params=params)

    def create_todo(self, text: str, alarm_at: str | None = None) -> dict:
        body: dict = {"text": text}
        if alarm_at is not None:
            body["alarm_at"] = alarm_at
        return self._request("POST", "/v1/todos", json_body=body)

    def get_todo(self, todo_id: int) -> dict:
        return self._request("GET", f"/v1/todos/{todo_id}")

    def delete_todo(self, todo_id: int) -> dict:
        """Verified endpoint (Phase 4B): DELETE /v1/todos/{id} -> 200
        {"success": true, "timezone": ...}. Send no body/Content-Type --
        a Content-Type header on this bodyless request triggers
        FST_ERR_CTP_EMPTY_JSON_BODY."""
        return self._request("DELETE", f"/v1/todos/{todo_id}")
