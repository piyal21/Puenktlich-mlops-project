import pandas as pd

from dbdelay.data.quality import QUARANTINE_REASONS, QualityReport, build_report, content_hash
from dbdelay.data.silver import ConformResult
from tests.builders import silver_frame


def _silver_for_month() -> pd.DataFrame:
    """Frankfurt: 10 departures/day at 07:00-07:09 UTC on every March day except 20 (1)."""
    frames = []
    for day in pd.date_range("2026-03-01", "2026-03-31"):
        n = 1 if day.day == 20 else 10
        frames.append(silver_frame(n, day=day.strftime("%Y-%m-%d")))
    return pd.concat(frames, ignore_index=True)


def _report(silver: pd.DataFrame, quarantine: pd.DataFrame | None = None) -> QualityReport:
    if quarantine is None:
        quarantine = pd.DataFrame({"quarantine_reason": pd.Series([], dtype="object")})
    return build_report(
        "2026-03",
        rows_read=500,
        conform=ConformResult(silver=silver, quarantine=quarantine, drops={"duplicate": 2}),
        edge_complete={"prev": True, "next": False},
        station_evas=["8000105", "8000261"],
    )


def test_counts_rates_and_passthrough_fields() -> None:
    silver = _silver_for_month()
    silver.loc[0, "is_late"] = True
    report = _report(silver)
    assert report.rows_read == 500
    assert report.rows_out == len(silver) == 301
    assert report.drops == {"duplicate": 2}
    assert report.edge_complete == {"prev": True, "next": False}
    assert report.late_rate == 1 / 301
    assert report.cancelled_rate == 0.0
    assert report.null_rates["line_number"] == 1.0
    assert report.null_rates["eva"] == 0.0
    assert report.content_hash == content_hash(silver)
    assert report.schema_version == 1


def test_quarantine_counts_list_every_reason() -> None:
    quarantine = pd.DataFrame({"quarantine_reason": ["bad_id", "bad_id", "delay_mismatch_utc"]})
    report = _report(_silver_for_month(), quarantine)
    assert report.quarantined["bad_id"] == 2
    assert report.quarantined["delay_mismatch_utc"] == 1
    assert set(QUARANTINE_REASONS) <= set(report.quarantined)


def test_volume_drop_days_and_silent_stations() -> None:
    report = _report(_silver_for_month())
    frankfurt = next(s for s in report.stations if s.eva == "8000105")
    munich = next(s for s in report.stations if s.eva == "8000261")
    assert frankfurt.rows == 301
    assert frankfurt.volume_drop_days == ["2026-03-20"]
    assert munich.rows == 0
    assert munich.volume_drop_days == []


def test_low_volume_hours_flag_collection_gaps() -> None:
    report = _report(_silver_for_month())
    # silver_frame rows sit at 07:00 UTC = 08:00 CET; the thin day is the 20th.
    assert "2026-03-20T08" in report.low_volume_hours


def test_empty_month_has_no_rates_and_no_flags() -> None:
    report = _report(silver_frame(0))
    assert report.rows_out == 0
    assert report.late_rate is None
    assert report.cancelled_rate is None
    assert report.low_volume_hours == []
    assert [s.rows for s in report.stations] == [0, 0]
