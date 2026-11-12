from datetime import UTC, datetime
from pathlib import Path

import pytest

from dbdelay.errors import DataValidationError
from dbdelay.timetables.parser import parse_db_time, parse_timetable

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
KIEL = "8000199"


def _plan() -> bytes:
    return (FIXTURES / "sample_plan.xml").read_bytes()


def _fchg() -> bytes:
    return (FIXTURES / "sample_fchg.xml").read_bytes()


def test_plan_fixture_parses_departures() -> None:
    result = parse_timetable(_plan(), KIEL)
    by_id = {e.stop_id: e for e in result.events}
    stop = by_id["-7979240743321752940-2609251743-1"]
    assert stop.ride_id == "-7979240743321752940-2609251743"
    assert stop.stop_index == 1
    assert stop.eva == KIEL
    assert stop.train_type == "ERX"
    assert stop.train_number == "21031"
    assert stop.line_number == "RE83"
    assert stop.planned_utc == datetime(2026, 9, 25, 15, 43, tzinfo=UTC)
    assert stop.changed_utc is None
    assert stop.planned_path[-1] == "Lübeck Hbf"
    assert stop.changed_path is None
    assert stop.distant_endpoint == "Lüneburg"
    assert stop.planned_platform == "1"
    assert not stop.cancelled
    # 19 stops, 9 with a departure; arrival-only stops are counted, not returned.
    assert len(result.events) == 9
    assert result.drops["no_departure"] == 10


def test_fchg_fixture_departure_cancellations() -> None:
    result = parse_timetable(_fchg(), KIEL)
    # The fixture has 5 cancelled stops; 2 of them are departures (3 are arrivals only).
    assert sum(e.cancelled for e in result.events) == 2
    cancelled = next(e for e in result.events if e.cancelled)
    assert cancelled.changed_path == ()  # cpth="" on a cancelled departure


def test_fchg_changed_time_and_missing_train_info() -> None:
    result = parse_timetable(_fchg(), KIEL)
    stop = next(e for e in result.events if e.stop_id == "-2398768980639512345-2609251603-1")
    assert stop.changed_utc == datetime(2026, 9, 25, 14, 3, tzinfo=UTC)
    assert stop.planned_utc is None  # fchg often lacks the planned time
    assert stop.train_type is None  # and the train info


def test_parse_db_time_dst() -> None:
    assert parse_db_time("2610250230") is None  # 2026-10-25 02:30 happens twice
    assert parse_db_time("2703280230") is None  # 2027-03-28 02:30 does not exist
    assert parse_db_time("2609251705") == datetime(2026, 9, 25, 15, 5, tzinfo=UTC)
    assert parse_db_time("2701151705") == datetime(2027, 1, 15, 16, 5, tzinfo=UTC)


def test_ambiguous_planned_time_is_dropped_and_counted() -> None:
    xml = (
        b'<timetable station="X"><s id="1-2610250200-1"><tl c="RE" n="1"/>'
        b'<dp pt="2610250230" ppth="A|B"/></s></timetable>'
    )
    result = parse_timetable(xml, KIEL)
    assert result.events == ()
    assert result.drops["dst_ambiguous_or_nonexistent"] == 1


@pytest.mark.parametrize(
    "body",
    [
        b"<timetable><s id=",  # truncated
        b"<html><body>Gateway error</body></html>",  # HTTP 200 with an error page
        b"",
    ],
)
def test_garbage_raises(body: bytes) -> None:
    with pytest.raises(DataValidationError):
        parse_timetable(body, KIEL)


def test_entity_bomb_raises() -> None:
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        b"<timetable>&lol2;</timetable>"
    )
    with pytest.raises(DataValidationError):
        parse_timetable(bomb, KIEL)


def test_bad_id_counted() -> None:
    xml = b'<timetable><s id="x"><dp pt="2609251705"/></s></timetable>'
    result = parse_timetable(xml, KIEL)
    assert result.events == ()
    assert result.drops["bad_id"] == 1


def test_changed_platform_path_and_category_normalised() -> None:
    xml = (
        b'<timetable><s id="-5-2609251700-3"><tl c=" ice " n="602"/>'
        b'<dp pt="2609251705" ct="2609251712" pp="2" cp="4" ppth="A|B" cpth="A|C|D" l="X"/>'
        b"</s></timetable>"
    )
    (stop,) = parse_timetable(xml, KIEL).events
    assert stop.train_type == "ICE"
    assert stop.stop_index == 3
    assert stop.ride_id == "-5-2609251700"
    assert stop.changed_utc == datetime(2026, 9, 25, 15, 12, tzinfo=UTC)
    assert stop.changed_platform == "4"
    assert stop.changed_path == ("A", "C", "D")
    assert stop.planned_path == ("A", "B")
