"""Rows that fall into flagged ingestion gaps (Phase 3 rule; shared by training and monitoring).

HF monthly reports and live daily reports both provide the two fields `gap_masks` reads.
"""

from collections.abc import Sequence
from typing import Protocol

import numpy as np
import pandas as pd

from dbdelay.data.quality import StationQuality
from dbdelay.features.calendar import BERLIN


class GapReport(Protocol):
    """What `gap_masks` reads: HF monthly and live daily quality reports both provide it."""

    @property
    def low_volume_hours(self) -> Sequence[str]: ...

    @property
    def stations(self) -> Sequence[StationQuality]: ...


def gap_masks(
    df: pd.DataFrame, reports: Sequence[GapReport]
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
