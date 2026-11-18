"""Data and prediction drift: Evidently's default tests for large samples, computed with scipy.

Numeric columns: Wasserstein distance normed by the reference standard deviation (floored at
0.001). Categorical columns: Jensen-Shannon distance (base 2) over the union of observed levels.
A column is drifted when its score >= ``drift_threshold`` (ADR 0003, spec §4.1).

Only the seven content columns count toward ``drift_share``: one day of data against a
multi-month reference makes the calendar columns drift by construction (spec §4.1).
"""

from typing import Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict
from scipy.spatial.distance import jensenshannon
from scipy.stats import wasserstein_distance

from dbdelay.monitoring.config import MonitoringConfig

CONTENT_CATEGORICAL: tuple[str, ...] = ("eva", "train_type", "line_key", "destination_key")
CONTENT_NUMERIC: tuple[str, ...] = ("stop_index", "hour_local", "minute_of_day")
CALENDAR_COLUMNS: tuple[str, ...] = ("weekday", "is_weekend", "month", "is_public_holiday")
STD_FLOOR = 0.001

Method = Literal["wasserstein_normed", "jensen_shannon"]
DayType = Literal["weekday", "weekend_or_holiday"]


class ColumnDrift(BaseModel):
    model_config = ConfigDict(frozen=True)

    column: str
    method: Method
    score: float
    drifted: bool
    counted: bool


class DriftResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    columns: list[ColumnDrift]
    drift_share: float
    prediction: ColumnDrift
    reference_rows: int
    current_rows: int
    day_type: DayType


def _off_day(features: pd.DataFrame) -> "pd.Series[bool]":
    return features["is_weekend"].astype(bool) | features["is_public_holiday"].astype(bool)


def day_type(features: pd.DataFrame) -> DayType:
    """Weekend/holiday when most rows fall on such a day (one UTC day can span two local days)."""
    return "weekend_or_holiday" if float(_off_day(features).mean()) > 0.5 else "weekday"  # noqa: PLR2004


def wasserstein_normed(current: NDArray[np.float64], reference: NDArray[np.float64]) -> float:
    norm = max(float(np.std(reference)), STD_FLOOR)
    return float(wasserstein_distance(current, reference) / norm)


def jensen_shannon(current: "pd.Series[str]", reference: "pd.Series[str]") -> float:
    levels = sorted(set(current.unique()) | set(reference.unique()))
    p = current.value_counts(normalize=True).reindex(levels, fill_value=0.0).to_numpy()
    q = reference.value_counts(normalize=True).reindex(levels, fill_value=0.0).to_numpy()
    return float(jensenshannon(p, q, base=2))


def _column(
    name: str, method: Method, current: pd.Series, reference: pd.Series, cfg: MonitoringConfig
) -> ColumnDrift:
    if method == "wasserstein_normed":
        score = wasserstein_normed(current.to_numpy(dtype=float), reference.to_numpy(dtype=float))
    else:
        score = jensen_shannon(current.astype(str), reference.astype(str))
    return ColumnDrift(
        column=name,
        method=method,
        score=score,
        drifted=score >= cfg.drift_threshold,
        counted=name not in CALENDAR_COLUMNS,
    )


def compute_drift(
    current: pd.DataFrame,
    reference: pd.DataFrame,
    current_p: NDArray[np.float64],
    reference_p: NDArray[np.float64],
    cfg: MonitoringConfig,
) -> DriftResult:
    """Drift of one day's model inputs and ``p_late`` against the champion's reference sample.

    Both frames are ``build_features`` output. The reference is narrowed to the current day
    type (weekday vs weekend/holiday); an empty match falls back to the whole reference.

    Raises:
        ValueError: if ``current`` has no rows.
    """
    if current.empty:
        raise ValueError("no current rows to compare")
    kind = day_type(current)
    mask = (_off_day(reference) == (kind == "weekend_or_holiday")).to_numpy()
    if not mask.any():
        mask = np.ones(len(reference), dtype=bool)
    ref = reference.loc[mask]
    ref_p = np.asarray(reference_p, dtype=float)[mask]

    columns = [
        _column(name, "jensen_shannon", current[name], ref[name], cfg)
        for name in (*CONTENT_CATEGORICAL, *CALENDAR_COLUMNS)
    ] + [
        _column(name, "wasserstein_normed", current[name], ref[name], cfg)
        for name in CONTENT_NUMERIC
    ]
    counted = [c for c in columns if c.counted]
    prediction = _column(
        "p_late",
        "wasserstein_normed",
        pd.Series(np.asarray(current_p, dtype=float)),
        pd.Series(ref_p),
        cfg,
    )
    return DriftResult(
        columns=columns,
        drift_share=sum(c.drifted for c in counted) / len(counted),
        prediction=prediction.model_copy(update={"counted": False}),
        reference_rows=int(mask.sum()),
        current_rows=len(current),
        day_type=kind,
    )
