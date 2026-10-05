"""Probability metrics for the late label: overall, calibration bins and slices."""

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike, NDArray
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

from dbdelay.training.report import (
    CalibrationBin,
    EvaluationReport,
    Metrics,
    SplitReport,
    TrainingReport,
)

__all__ = [
    "EPS",
    "HOUR_BANDS",
    "SLICE_COLUMNS",
    "CalibrationBin",
    "EvaluationReport",
    "Metrics",
    "SplitReport",
    "TrainingReport",
    "calibration_bins",
    "compute_metrics",
    "evaluate_split",
    "expected_calibration_error",
    "hour_band",
    "slice_frame",
]

EPS = 1e-6
HOUR_BANDS: tuple[tuple[int, int, str], ...] = (
    (0, 5, "00-05"),
    (6, 9, "06-09"),
    (10, 15, "10-15"),
    (16, 19, "16-19"),
    (20, 23, "20-23"),
)
SLICE_COLUMNS: tuple[str, ...] = ("train_type", "eva", "hour_band")


def calibration_bins(y_true: ArrayLike, y_prob: ArrayLike, bins: int) -> list[CalibrationBin]:
    """Equal-width probability bins (non-empty only): mean prediction vs observed late rate."""
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_prob, dtype=float)
    index = np.minimum((p * bins).astype(np.int64), bins - 1)
    result = []
    for b in range(bins):
        mask = index == b
        if mask.any():
            result.append(
                CalibrationBin(
                    lower=b / bins,
                    upper=(b + 1) / bins,
                    mean_pred=float(p[mask].mean()),
                    observed_rate=float(y[mask].mean()),
                    count=int(mask.sum()),
                )
            )
    return result


def expected_calibration_error(y_true: ArrayLike, y_prob: ArrayLike, bins: int) -> float:
    """Bin-size-weighted mean |mean prediction - observed rate|."""
    n = len(np.asarray(y_true))
    return float(
        sum(
            b.count / n * abs(b.mean_pred - b.observed_rate)
            for b in calibration_bins(y_true, y_prob, bins)
        )
    )


def compute_metrics(
    y_true: ArrayLike, y_prob: ArrayLike, *, ece_bins: int, min_rows: int
) -> Metrics:
    """All metrics, or only ``n``/``base_rate`` when too few rows or a single class."""
    y = np.asarray(y_true, dtype=bool)
    p = np.asarray(y_prob, dtype=float)
    n = len(y)
    if n == 0:
        return Metrics(n=0, base_rate=None)
    base_rate = float(y.mean())
    if n < min_rows or y.all() or not y.any():
        return Metrics(n=n, base_rate=base_rate)
    y_int = y.astype(int)
    return Metrics(
        n=n,
        base_rate=base_rate,
        brier=float(brier_score_loss(y_int, p)),
        roc_auc=float(roc_auc_score(y_int, p)),
        pr_auc=float(average_precision_score(y_int, p)),
        log_loss=float(log_loss(y_int, np.clip(p, EPS, 1 - EPS), labels=[0, 1])),
        ece=expected_calibration_error(y, p, ece_bins),
    )


def hour_band(hours: pd.Series) -> pd.Series:
    """Map local hours to the five slice bands."""
    values = hours.to_numpy()
    labels = np.full(len(values), "", dtype=object)
    for low, high, name in HOUR_BANDS:
        labels[(values >= low) & (values <= high)] = name
    return pd.Series(labels, index=hours.index)


def slice_frame(features: pd.DataFrame) -> pd.DataFrame:
    """Slice keys for ``evaluate_split`` from a feature frame."""
    return pd.DataFrame(
        {
            "train_type": features["train_type"].astype(str).to_numpy(),
            "eva": features["eva"].astype(str).to_numpy(),
            "hour_band": hour_band(features["hour_local"]).to_numpy(),
        }
    )


def evaluate_split(
    y_true: ArrayLike,
    y_prob: ArrayLike,
    slices: pd.DataFrame,
    *,
    ece_bins: int,
    slice_min_rows: int,
) -> SplitReport:
    """Overall metrics, calibration bins and per-slice metrics for one split."""
    y: NDArray[np.bool_] = np.asarray(y_true, dtype=bool)
    p: NDArray[np.float64] = np.asarray(y_prob, dtype=float)
    per_slice: dict[str, dict[str, Metrics]] = {}
    for column in SLICE_COLUMNS:
        keys = slices[column].astype(str).to_numpy()
        per_slice[column] = {
            value: compute_metrics(
                y[keys == value], p[keys == value], ece_bins=ece_bins, min_rows=slice_min_rows
            )
            for value in sorted(set(keys.tolist()))
        }
    return SplitReport(
        overall=compute_metrics(y, p, ece_bins=ece_bins, min_rows=1),
        calibration=calibration_bins(y, p, ece_bins),
        slices=per_slice,
    )
