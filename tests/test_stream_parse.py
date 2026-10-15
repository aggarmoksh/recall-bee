from pathlib import Path

from recall.bee import stream as stream_module
from recall.bee.stream import parse_line

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "stream_sample_synthetic.ndjson"


def _fixture_lines() -> list[str]:
    return FIXTURE_PATH.read_text(encoding="utf-8").splitlines()


def test_fixture_file_has_expected_variety():
    lines = _fixture_lines()
    assert len(lines) >= 10


def test_no_crash_on_any_fixture_line():
    for line in _fixture_lines():
        parse_line(line)  # must not raise


def test_malformed_json_returns_none():
    assert parse_line("not valid json {{{") is None


def test_blank_line_returns_none():
    assert parse_line("") is None
    assert parse_line("   ") is None


def test_heartbeat_has_no_type_and_is_still_returned():
    event = parse_line('{"timestamp":1790247995789}')
    assert event is not None
    assert "type" not in event


def test_known_event_types_are_parsed():
    seen_types = set()
    for line in _fixture_lines():
        event = parse_line(line)
        if event and "type" in event:
            seen_types.add(event["type"])
    expected = {"new-utterance", "new-conversation", "new-utterance-chunks", "update-conversation", "todo-deleted"}
    assert expected.issubset(seen_types)


def test_unknown_event_type_does_not_crash():
    event = parse_line('{"type":"some-totally-new-event-type","payload":{"nested":[1,2,3]}}')
    assert event is not None
    assert event["type"] == "some-totally-new-event-type"


# Phase 5G Step 4 (AGENT.md, 2026-10-07): regression tests for the 2-day
# check's second crash -- a single long stream line exceeded asyncio's
# default readline() buffer and the resulting ValueError propagated all
# the way out, killing the whole daemon (FRICTION_LOG.md Incident 2).


class _FakeStdout:
    def __init__(self, lines: list[bytes], raise_once_at: int | None = None):
        self._lines = list(lines)
        self._raise_once_at = raise_once_at
        self._calls = 0

    async def readline(self) -> bytes:
        if self._raise_once_at is not None and self._calls == self._raise_once_at:
            self._calls += 1
            raise ValueError("Separator is not found, and chunk exceed the limit")
        self._calls += 1
        if not self._lines:
            return b""
        return self._lines.pop(0)


class _FakeProc:
    def __init__(self, lines: list[bytes], raise_once_at: int | None = None):
        self.pid = 999999
        self.stdout = _FakeStdout(lines, raise_once_at=raise_once_at)


def _patch_subprocess_plumbing(monkeypatch, procs: list[_FakeProc]):
    captured_kwargs = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return procs.pop(0)

    monkeypatch.setattr(stream_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(stream_module, "resolve_bee_exe", lambda: "bee")
    monkeypatch.setattr(stream_module, "warn_on_orphans", lambda: None)
    monkeypatch.setattr(stream_module, "kill_tree", lambda pid: None)
    monkeypatch.setattr(stream_module.asyncio, "sleep", _instant_sleep)
    return captured_kwargs


async def _instant_sleep(_seconds):
    return None


async def test_stream_events_parses_a_100kb_line(monkeypatch):
    big_text = "x" * (100 * 1024)
    line = (f'{{"type":"new-utterance","text":"{big_text}"}}\n').encode()
    procs = [_FakeProc([line, b""])]
    captured_kwargs = _patch_subprocess_plumbing(monkeypatch, procs)

    gen = stream_module.stream_events()
    event = await gen.__anext__()
    await gen.aclose()

    assert len(event["text"]) == 100 * 1024
    assert captured_kwargs["limit"] == stream_module.STREAM_READ_BUFFER_LIMIT


async def test_stream_events_reconnects_after_a_reader_exception(monkeypatch):
    good_line = b'{"type":"new-utterance","text":"before the crash"}\n'
    after_reconnect = b'{"type":"new-utterance","text":"after reconnect"}\n'
    procs = [
        _FakeProc([good_line], raise_once_at=1),  # readline() raises on its 2nd call
        _FakeProc([after_reconnect, b""]),
    ]
    _patch_subprocess_plumbing(monkeypatch, procs)

    gen = stream_module.stream_events()
    first = await gen.__anext__()
    second = await gen.__anext__()
    await gen.aclose()

    assert first["text"] == "before the crash"
    assert second["text"] == "after reconnect"  # reconnected instead of propagating the exception
