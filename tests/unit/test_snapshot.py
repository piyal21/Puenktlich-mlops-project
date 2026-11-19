from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from dbdelay.data.live_report import LiveQualityReport, live_quality_key, live_silver_key
from dbdelay.data.silver import (
    SILVER_ARROW_SCHEMA,
    quality_key,
    to_parquet_bytes,
    write_silver_month,
)
from dbdelay.errors import DataValidationError
from dbdelay.storage import ObjectStore
from dbdelay.training.split import (
    build_snapshot,
    latest_silver_day,
    load_snapshot_split,
    snapshot_prefix,
)
from tests.builders import MARCH_CONFIG, put_march_silver, quality_report, silver_day


def test_snapshot_rows_exclusions_and_files(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    manifest = build_snapshot(s3_store, MARCH_CONFIG, tmp_path)

    assert manifest.snapshot_id == f"2026-03-31_{manifest.content_hash[:8]}"
    assert (manifest.window_start, manifest.window_end) == (date(2026, 3, 1), date(2026, 3, 31))
    assert {k: v.rows for k, v in manifest.splits.items()} == {"train": 63, "valid": 24, "test": 28}
    assert manifest.splits["train"].late_rate == pytest.approx(31 / 63)
    assert manifest.splits["test"].late_rate == pytest.approx(0.5)
    assert manifest.excluded == {"cancelled": 1, "gap_hours": 4, "gap_station_days": 4}
    assert manifest.silver_days == 31
    assert manifest.quality_months == ["2026-03"]
    prefix = snapshot_prefix(manifest.snapshot_id)
    for name in ("train.parquet", "valid.parquet", "test.parquet", "snapshot.json"):
        assert s3_store.exists(prefix + name)

    test = load_snapshot_split(s3_store, manifest.snapshot_id, "test", tmp_path)
    assert len(test) == 28
    assert test["event_id"].is_monotonic_increasing
    assert test["is_late"].notna().all()


def test_snapshot_is_deterministic_and_idempotent(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    first = build_snapshot(s3_store, MARCH_CONFIG, tmp_path / "a")
    key = snapshot_prefix(first.snapshot_id) + "train.parquet"
    before = s3_store.get_bytes(key)
    second = build_snapshot(s3_store, MARCH_CONFIG, tmp_path / "b")
    assert second == first
    assert s3_store.get_bytes(key) == before


def test_end_date_defaults_to_latest_silver_day(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    assert latest_silver_day(s3_store) == date(2026, 3, 31)
    cfg = MARCH_CONFIG.model_copy(update={"end_date": None})
    assert build_snapshot(s3_store, cfg, tmp_path).window_end == date(2026, 3, 31)


def test_gap_exclusion_can_be_switched_off(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store, with_report=False)
    cfg = MARCH_CONFIG.model_copy(update={"exclude_data_gaps": False})
    manifest = build_snapshot(s3_store, cfg, tmp_path)
    assert manifest.excluded == {"cancelled": 1, "gap_hours": 0, "gap_station_days": 0}
    assert manifest.quality_months == []


def test_missing_quality_report_is_an_error(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store, with_report=False)
    with pytest.raises(DataValidationError, match="quality report"):
        build_snapshot(s3_store, MARCH_CONFIG, tmp_path)


def test_missing_silver_day_is_an_error(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    cfg = MARCH_CONFIG.model_copy(update={"end_date": date(2026, 4, 2)})
    with pytest.raises(DataValidationError, match="silver day"):
        build_snapshot(s3_store, cfg, tmp_path)


def test_empty_split_is_an_error(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    cfg = MARCH_CONFIG.model_copy(update={"test_days": 1, "valid_days": 1})
    # valid = 2026-03-30 only; flag that station-day so the valid split is empty.
    report = quality_report(
        "2026-03",
        low_volume_hours=["2026-03-16T08"],
        drop_days={"8000105": ["2026-03-20", "2026-03-30"]},
    )
    s3_store.put_bytes(quality_key("2026-03"), report.model_dump_json().encode())
    with pytest.raises(DataValidationError, match="valid"):
        build_snapshot(s3_store, cfg, tmp_path)


def test_no_silver_at_all_is_an_error(s3_store: ObjectStore, tmp_path: Path) -> None:
    with pytest.raises(DataValidationError, match="no silver"):
        latest_silver_day(s3_store)


def test_manifest_records_split_config(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    manifest = build_snapshot(s3_store, MARCH_CONFIG, tmp_path)
    assert manifest.config == {
        "window_months": 1,
        "end_date": "2026-03-31",
        "test_days": 7,
        "valid_days": 7,
        "exclude_data_gaps": True,
    }


def test_latest_silver_day_skips_empty_partitions(s3_store: ObjectStore) -> None:
    put_march_silver(s3_store)
    april = pd.concat([silver_day(f"2026-04-0{d}") for d in range(1, 6)], ignore_index=True)
    write_silver_month(april, s3_store, "2026-04")  # writes all 30 April days, 25 of them empty
    assert latest_silver_day(s3_store) == date(2026, 4, 5)


def test_only_empty_partitions_is_an_error(s3_store: ObjectStore) -> None:
    write_silver_month(silver_day("2026-04-01").iloc[0:0], s3_store, "2026-04")
    with pytest.raises(DataValidationError, match="no silver"):
        latest_silver_day(s3_store)


def _put_live(store: ObjectStore, day: str, n: int, **report: object) -> None:
    rows = silver_day(day, n, hour_utc=9)
    rows["source"] = "live"
    store.put_bytes(
        live_silver_key(date.fromisoformat(day)), to_parquet_bytes(rows, SILVER_ARROW_SCHEMA)
    )
    store.put_bytes(
        live_quality_key(date.fromisoformat(day)),
        _live_report(date.fromisoformat(day), **report).model_dump_json().encode(),
    )


def _live_report(day: date, **kw: object) -> LiveQualityReport:
    base: dict[str, object] = {
        "day": day,
        "rows_out": 0,
        "drops": {},
        "late_rate": None,
        "cancelled_rate": None,
        "stations": [],
        "low_volume_hours": [],
        "fchg_runs_ok": {},
        "longest_fchg_gap_min": {},
        "plan_hours_missing": {},
        "never_seen_in_fchg": 0,
        "fchg_only_stops": 0,
        "empty_stations": [],
        "content_hash": "x",
    }
    return LiveQualityReport.model_validate(base | kw)


def test_live_wins_on_overlap(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    _put_live(s3_store, "2026-03-31", 6)
    manifest = build_snapshot(s3_store, MARCH_CONFIG, tmp_path)
    assert manifest.splits["test"].rows == 28 - 4 + 6
    assert manifest.rows_by_source == {"hf": 63 + 24 + 24, "live": 6}
    test = load_snapshot_split(s3_store, manifest.snapshot_id, "test", tmp_path)
    assert set(test.loc[test["planned_departure_utc"].dt.day == 31, "source"]) == {"live"}


def test_rows_by_source_hf_only(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    manifest = build_snapshot(s3_store, MARCH_CONFIG, tmp_path)
    assert manifest.rows_by_source == {"hf": 63 + 24 + 28}
    assert manifest.quality_months == ["2026-03"]


def test_sources_change_the_snapshot_id(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    hf_only = build_snapshot(s3_store, MARCH_CONFIG, tmp_path / "a")
    _put_live(s3_store, "2026-03-31", 4)
    with_live = build_snapshot(s3_store, MARCH_CONFIG, tmp_path / "b")
    assert with_live.snapshot_id != hf_only.snapshot_id


def test_live_quality_gaps_excluded(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    _put_live(s3_store, "2026-03-31", 6, low_volume_hours=["2026-03-31T11"])
    manifest = build_snapshot(s3_store, MARCH_CONFIG, tmp_path)
    # The live rows run 09:00-09:05 UTC = 11:00 local (CEST since 29 March): all 6 flagged.
    assert manifest.excluded["gap_hours"] == 4 + 6
    assert manifest.splits["test"].rows == 24


def test_missing_live_report_is_an_error(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    _put_live(s3_store, "2026-03-31", 6)
    s3_store._client.delete_object(Bucket=s3_store.bucket, Key=live_quality_key(date(2026, 3, 31)))
    with pytest.raises(DataValidationError, match="quality report"):
        build_snapshot(s3_store, MARCH_CONFIG, tmp_path)


def test_latest_day_across_sources(s3_store: ObjectStore) -> None:
    put_march_silver(s3_store)
    _put_live(s3_store, "2026-04-02", 3)
    assert latest_silver_day(s3_store) == date(2026, 4, 2)


def test_live_fills_a_day_hf_lacks(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    _put_live(s3_store, "2026-04-01", 5)
    cfg = MARCH_CONFIG.model_copy(update={"end_date": date(2026, 4, 1)})
    manifest = build_snapshot(s3_store, cfg, tmp_path)
    assert manifest.rows_by_source["live"] == 5
