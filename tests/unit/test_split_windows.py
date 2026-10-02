from datetime import date

import pandas as pd
import pytest

from dbdelay.errors import DataValidationError
from dbdelay.training.config import (
    BaselineConfig,
    EvaluationConfig,
    FeatureConfig,
    TrainingConfig,
)
from dbdelay.training.split import assign_split, compute_windows, gap_masks
from tests.builders import quality_report


def _cfg(window_months: int = 9, test_days: int = 14, valid_days: int = 14) -> TrainingConfig:
    return TrainingConfig(
        window_months=window_months,
        test_days=test_days,
        valid_days=valid_days,
        exclude_data_gaps=True,
        features=FeatureConfig(min_count=1),
        baseline=BaselineConfig(min_count=1),
        evaluation=EvaluationConfig(ece_bins=10, slice_min_rows=1),
    )


def test_default_window_matches_the_spec() -> None:
    windows = compute_windows(date(2026, 8, 31), _cfg())
    assert windows.start == date(2025, 12, 1)
    assert windows.valid_start == date(2026, 8, 4)
    assert windows.test_start == date(2026, 8, 18)
    assert windows.split_ranges() == {
        "train": (date(2025, 12, 1), date(2026, 8, 3)),
        "valid": (date(2026, 8, 4), date(2026, 8, 17)),
        "test": (date(2026, 8, 18), date(2026, 8, 31)),
    }
    assert len(windows.days()) == 274
    assert windows.months() == [
        "2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
        "2026-05", "2026-06", "2026-07", "2026-08",
    ]  # fmt: skip


def test_window_start_clamps_to_month_end() -> None:
    assert compute_windows(date(2026, 5, 31), _cfg(window_months=3)).start == date(2026, 3, 1)
    assert compute_windows(date(2026, 3, 31), _cfg(window_months=1)).start == date(2026, 3, 1)


def test_window_too_short_for_valid_and_test_raises() -> None:
    with pytest.raises(DataValidationError, match="too short"):
        compute_windows(date(2026, 3, 31), _cfg(window_months=1, test_days=20, valid_days=11))


def test_assign_split_boundaries_are_utc_dates() -> None:
    windows = compute_windows(date(2026, 8, 31), _cfg())
    planned = pd.Series(
        pd.to_datetime(
            ["2026-08-03 23:59", "2026-08-04 00:00", "2026-08-17 23:59", "2026-08-18 00:00"],
            utc=True,
        )
    )
    assert assign_split(planned, windows).tolist() == ["train", "valid", "valid", "test"]


def test_gap_masks_match_local_hours_and_station_days() -> None:
    frame = pd.DataFrame(
        {
            "eva": ["8000105", "8000105", "8000105", "8000096"],
            "planned_departure_utc": pd.to_datetime(
                ["2026-03-16 17:30", "2026-03-16 16:30", "2026-03-23 10:00", "2026-03-23 10:00"],
                utc=True,
            ),
        }
    )
    report = quality_report(
        "2026-03", low_volume_hours=["2026-03-16T18"], drop_days={"8000105": ["2026-03-23"]}
    )
    in_hour, in_day = gap_masks(frame, [report])
    assert in_hour.tolist() == [True, False, False, False]
    assert in_day.tolist() == [False, False, True, False]


def test_gap_masks_without_flags_are_all_false() -> None:
    frame = pd.DataFrame(
        {
            "eva": ["8000105"],
            "planned_departure_utc": pd.to_datetime(["2026-03-16 17:30"], utc=True),
        }
    )
    in_hour, in_day = gap_masks(frame, [quality_report("2026-03")])
    assert not in_hour.any()
    assert not in_day.any()
