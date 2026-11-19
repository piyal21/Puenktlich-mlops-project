from datetime import date, timedelta

import pandas as pd
import pytest

from dbdelay.data.months import month_days
from dbdelay.data.quality import gap_fields_for_days
from dbdelay.data.silver import quality_key, silver_key
from dbdelay.errors import DataValidationError
from dbdelay.live.hf_raw_fill import FILL_MARKER, fill_hf_raw_days
from dbdelay.storage import ObjectStore
from tests.builders import quality_report, read_parquet_bytes, silver_day

EVAS = ["8000105", "8000261"]
DAYS = [date(2026, 11, 1) + timedelta(days=i) for i in range(4)]


def _builder(day: date) -> tuple[pd.DataFrame, dict[str, int]]:
    n = 2 if day == date(2026, 11, 3) else 10  # 8000105 drops on the 3rd
    frame = pd.concat(
        [
            silver_day(f"{day:%Y-%m-%d}", n, eva="8000105"),
            silver_day(f"{day:%Y-%m-%d}", 10, eva="8000261"),
        ],
        ignore_index=True,
    )
    frame["source"] = "live"  # the live conform code labels rows as live
    return frame, {"responses": 5, "parse_errors": 0}


def test_gap_fields_for_days_uses_only_those_days() -> None:
    silver = pd.concat([_builder(d)[0] for d in DAYS], ignore_index=True)
    _, stations = gap_fields_for_days(silver, DAYS, EVAS)
    by_eva = {s.eva: s for s in stations}
    assert by_eva["8000105"].volume_drop_days == ["2026-11-03"]
    assert by_eva["8000261"].volume_drop_days == []
    # Over the whole month the empty days would set the median to 0 → nothing flagged.
    _, month_stations = gap_fields_for_days(silver, month_days("2026-11"), EVAS)
    assert all(not s.volume_drop_days for s in month_stations)


def test_fill_writes_days_and_report(s3_store: ObjectStore) -> None:
    report = fill_hf_raw_days(s3_store, EVAS, DAYS, _builder)
    for day in DAYS:
        stored = read_parquet_bytes(s3_store.get_bytes(silver_key(day)))
        assert set(stored["source"]) == {"hf"}
    assert report.month == "2026-11"
    assert report.drops[FILL_MARKER] == 4
    assert report.rows_read == 20
    assert report.rows_out == 10 + 10 + 2 + 10 + 3 * 10 + 10
    flagged = {s.eva: s.volume_drop_days for s in report.stations}
    assert flagged["8000105"] == ["2026-11-03"]
    stored_report = s3_store.get_bytes(quality_key("2026-11"))
    assert report.model_validate_json(stored_report) == report


def test_fill_can_replace_its_own_report(s3_store: ObjectStore) -> None:
    fill_hf_raw_days(s3_store, EVAS, DAYS[:2], _builder)
    report = fill_hf_raw_days(s3_store, EVAS, DAYS, _builder)
    assert report.drops[FILL_MARKER] == 4


def test_fill_refuses_real_monthly_report(s3_store: ObjectStore) -> None:
    s3_store.put_bytes(quality_key("2026-11"), quality_report("2026-11").model_dump_json().encode())
    with pytest.raises(DataValidationError, match="monthly report"):
        fill_hf_raw_days(s3_store, EVAS, DAYS, _builder)


def test_fill_refuses_two_months(s3_store: ObjectStore) -> None:
    with pytest.raises(DataValidationError, match="one month"):
        fill_hf_raw_days(s3_store, EVAS, [date(2026, 10, 31), date(2026, 11, 1)], _builder)
