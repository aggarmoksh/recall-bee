import httpx
import pytest

from recall.bee.client import BeeClient


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr("recall.bee.client.time.sleep", lambda _: None)


@pytest.fixture(autouse=True)
def isolated_log(tmp_path, monkeypatch):
    monkeypatch.setattr("recall.bee.client.LOG_PATH", tmp_path / "bee-events.ndjson")


def _client_with_transport(handler) -> BeeClient:
    client = BeeClient(base_url="http://127.0.0.1:8787")
    client._client = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(handler))
    return client


def test_post_sends_exact_charset_header():
    seen_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers["content-type"] = request.headers.get("content-type")
        return httpx.Response(200, json={"id": 1, "text": "hi", "completed": False})

    client = _client_with_transport(handler)
    client.create_todo("hi", alarm_at="2026-01-01T00:00:00-07:00")
    assert seen_headers["content-type"] == "application/json; charset=utf-8"


def test_get_request_has_no_forced_charset_header():
    seen_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers["content-type"] = request.headers.get("content-type")
        return httpx.Response(200, json={"id": 52223})

    client = _client_with_transport(handler)
    client.get_me()
    assert seen_headers["content-type"] is None


def test_retries_on_503_then_succeeds():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] < 3:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json={"id": 52223})

    client = _client_with_transport(handler)
    result = client.get_me()
    assert result == {"id": 52223}
    assert call_count["n"] == 3


def test_does_not_retry_on_404():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(404, json={"error": "not found"})

    client = _client_with_transport(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_todo(999)
    assert call_count["n"] == 1


def test_exhausts_retries_and_raises_on_persistent_503():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(503, json={"error": "unavailable"})

    client = _client_with_transport(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_me()
    assert call_count["n"] == 3
