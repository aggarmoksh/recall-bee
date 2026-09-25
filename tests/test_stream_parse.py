from pathlib import Path

from recall.bee.stream import parse_line

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "bee_stream_sample.ndjson"


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
