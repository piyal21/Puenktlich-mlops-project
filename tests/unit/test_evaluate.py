import math

import numpy as np
import pandas as pd
import pytest

from dbdelay.training.evaluate import (
    calibration_bins,
    compute_metrics,
    evaluate_split,
    expected_calibration_error,
    hour_band,
    slice_frame,
)

Y = np.array([False, False, True, True])
P = np.array([0.1, 0.4, 0.35, 0.8])


def test_metrics_match_hand_computed_values() -> None:
    m = compute_metrics(Y, P, ece_bins=2, min_rows=1)
    assert m.n == 4
    assert m.base_rate == 0.5
    assert m.brier == pytest.approx(0.158125)
    assert m.roc_auc == pytest.approx(0.75)
    assert m.pr_auc == pytest.approx(0.8333333, rel=1e-6)
    assert m.log_loss == pytest.approx(0.4722880, rel=1e-6)
    assert m.ece == pytest.approx(0.0875)


def test_calibration_bins_and_ece() -> None:
    bins = calibration_bins(Y, P, 2)
    assert [(b.lower, b.upper, b.count) for b in bins] == [(0.0, 0.5, 3), (0.5, 1.0, 1)]
    assert bins[0].mean_pred == pytest.approx(0.85 / 3)
    assert bins[0].observed_rate == pytest.approx(1 / 3)
    assert expected_calibration_error(Y, P, 2) == pytest.approx(0.0875)
    assert calibration_bins(np.array([True]), np.array([1.0]), 10)[0].lower == pytest.approx(0.9)


def test_small_or_one_class_slices_report_null_metrics() -> None:
    small = compute_metrics(Y, P, ece_bins=10, min_rows=5)
    assert (small.n, small.base_rate, small.brier, small.roc_auc) == (4, 0.5, None, None)
    one_class = compute_metrics(Y[:2], P[:2], ece_bins=10, min_rows=1)
    assert one_class.roc_auc is None
    empty = compute_metrics(Y[:0], P[:0], ece_bins=10, min_rows=1)
    assert (empty.n, empty.base_rate) == (0, None)


def test_extreme_probabilities_give_finite_log_loss() -> None:
    m = compute_metrics(np.array([False, True]), np.array([0.0, 1.0]), ece_bins=10, min_rows=1)
    assert m.log_loss is not None
    assert math.isfinite(m.log_loss)
    assert m.brier == pytest.approx(0.0)


def test_hour_bands() -> None:
    hours = pd.Series([0, 5, 6, 9, 10, 15, 16, 19, 20, 23])
    assert hour_band(hours).tolist() == [
        "00-05", "00-05", "06-09", "06-09", "10-15",
        "10-15", "16-19", "16-19", "20-23", "20-23",
    ]  # fmt: skip


def test_evaluate_split_builds_overall_bins_and_slices() -> None:
    features = pd.DataFrame(
        {
            "train_type": pd.Categorical(["RB", "RB", "ICE", "ICE"]),
            "eva": pd.Categorical(["A", "A", "A", "B"]),
            "hour_local": [7, 7, 18, 18],
        }
    )
    report = evaluate_split(Y, P, slice_frame(features), ece_bins=2, slice_min_rows=2)
    assert report.overall.n == 4
    assert [b.count for b in report.calibration] == [3, 1]
    assert set(report.slices) == {"train_type", "eva", "hour_band"}
    assert list(report.slices["train_type"]) == ["ICE", "RB"]
    assert report.slices["eva"]["B"].n == 1
    assert report.slices["eva"]["B"].brier is None
    assert report.slices["hour_band"]["06-09"].roc_auc is None  # one class only
