from datetime import date
from pathlib import Path

import pytest

from dbdelay.data.silver import quality_key
from dbdelay.errors import DataValidationError
from dbdelay.storage import ObjectStore
from dbdelay.training.split import (
    build_snapshot,
    latest_silver_day,
    load_snapshot_split,
    snapshot_prefix,
)
from tests.builders import MARCH_CONFIG, put_march_silver, quality_report


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
