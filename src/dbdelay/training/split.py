"""Time-based train/valid/test snapshot of labelled silver rows (architecture §3.4, §5.3)."""

from calendar import monthrange
from collections.abc import Sequence
from datetime import date, timedelta

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from dbdelay.data.quality import QualityReport
from dbdelay.errors import DataValidationError
from dbdelay.features.calendar import BERLIN
from dbdelay.training.config import TrainingConfig

SPLITS: tuple[str, ...] = ("train", "valid", "test")


class Windows(BaseModel):
    """Inclusive UTC-date bounds of the snapshot window and its splits."""

    model_config = ConfigDict(frozen=True)

    start: date
    valid_start: date
    test_start: date
    end: date

    def split_ranges(self) -> dict[str, tuple[date, date]]:
        one = timedelta(days=1)
        return {
            "train": (self.start, self.valid_start - one),
            "valid": (self.valid_start, self.test_start - one),
            "test": (self.test_start, self.end),
        }

    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]

    def months(self) -> list[str]:
        return sorted({f"{day:%Y-%m}" for day in self.days()})


def _minus_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def compute_windows(end: date, cfg: TrainingConfig) -> Windows:
    """Window = the ``window_months`` ending on ``end``; test/valid are its last days.

    Raises:
        DataValidationError: if no train day would be left.
    """
    start = _minus_months(end, cfg.window_months) + timedelta(days=1)
    test_start = end - timedelta(days=cfg.test_days - 1)
    valid_start = test_start - timedelta(days=cfg.valid_days)
    if valid_start <= start:
        raise DataValidationError("window is too short for the valid and test splits")
    return Windows(start=start, valid_start=valid_start, test_start=test_start, end=end)


def gap_masks(
    df: pd.DataFrame, reports: Sequence[QualityReport]
) -> tuple["pd.Series[bool]", "pd.Series[bool]"]:
    """Rows in a flagged low-volume local hour, and rows on a station's flagged UTC day."""
    planned = df["planned_departure_utc"]
    gap_hours = pd.DatetimeIndex(
        [
            pd.Timestamp(f"{hour}:00").tz_localize(BERLIN)
            for report in reports
            for hour in report.low_volume_hours
        ],
        tz=BERLIN,
    )
    # Berlin offsets are whole hours, so flooring in UTC equals flooring in local time.
    local_hour = planned.dt.floor("h").dt.tz_convert(BERLIN)
    in_hour = local_hour.isin(gap_hours).to_numpy()

    drop_days: dict[str, set[str]] = {}
    for report in reports:
        for station in report.stations:
            drop_days.setdefault(station.eva, set()).update(station.volume_drop_days)
    utc_day = planned.dt.floor("D")
    evas = df["eva"].astype(str).to_numpy()
    in_day = np.zeros(len(df), dtype=bool)
    for eva, days in sorted(drop_days.items()):
        flagged = pd.DatetimeIndex(sorted(days)).tz_localize("UTC")
        in_day |= (evas == eva) & utc_day.isin(flagged).to_numpy()
    return (
        pd.Series(in_hour, index=df.index, dtype="bool"),
        pd.Series(in_day, index=df.index, dtype="bool"),
    )


def assign_split(planned_utc: pd.Series, windows: Windows) -> pd.Series:
    """``train`` / ``valid`` / ``test`` by the UTC date of the planned departure."""
    day = planned_utc.dt.floor("D")
    valid_start = pd.Timestamp(windows.valid_start.isoformat(), tz="UTC")
    test_start = pd.Timestamp(windows.test_start.isoformat(), tz="UTC")
    labels = np.where(day < valid_start, "train", np.where(day < test_start, "valid", "test"))
    return pd.Series(labels, index=planned_utc.index)
