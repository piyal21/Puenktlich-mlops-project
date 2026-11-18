from datetime import UTC, datetime

import pytest

from dbdelay.errors import DataValidationError
from dbdelay.timetables.bronze import BronzeRecord, bronze_key, iter_records, records_to_bytes

AT = datetime(2026, 11, 12, 8, 15, tzinfo=UTC)


def test_bronze_key_format() -> None:
    assert (
        bronze_key("fchg", AT)
        == "bronze/timetables/kind=fchg/date=2026-11-12/run=2026-11-12T081500Z.jsonl.gz"
    )


def test_bronze_roundtrip() -> None:
    records = [
        BronzeRecord("plan", "8000199", "26111209", AT, 200, "<timetable>ü</timetable>", None),
        BronzeRecord("fchg", "8000105", None, AT, None, None, "ConnectError"),
    ]
    data = records_to_bytes(records)
    assert list(iter_records(data)) == records
    assert records_to_bytes(records) == data  # byte-identical (gzip mtime=0)


def test_bronze_naive_time_rejected() -> None:
    with pytest.raises(ValueError, match="UTC"):
        BronzeRecord("fchg", "8000105", None, datetime(2026, 11, 12), 200, "", None)


def test_bronze_garbage_raises() -> None:
    with pytest.raises(DataValidationError):
        list(iter_records(b"not gzip"))


def test_bronze_body_with_unicode_line_separators_round_trips() -> None:
    body = "<timetable>a" + chr(0x2028) + "b" + chr(0x2029) + "c" + chr(0x85) + "d</timetable>"
    record = BronzeRecord("fchg", "8000105", None, AT, 200, body, None)
    assert list(iter_records(records_to_bytes([record]))) == [record]
