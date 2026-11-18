from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.spatial.distance import jensenshannon
from scipy.stats import wasserstein_distance

from dbdelay.monitoring.config import load_monitoring_config
from dbdelay.monitoring.drift import (
    CALENDAR_COLUMNS,
    CONTENT_CATEGORICAL,
    CONTENT_NUMERIC,
    compute_drift,
    day_type,
)

CFG = load_monitoring_config(Path(__file__).resolve().parents[2] / "configs" / "monitoring.yaml")


def _frame(n: int, seed: int, *, weekend: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "eva": pd.Categorical(rng.choice(["8000105", "8000261", "8011160"], n)),
            "train_type": pd.Categorical(rng.choice(["ICE", "RE", "S"], n)),
            "line_key": pd.Categorical(rng.choice(["RE1", "S8", "ICE 5"], n)),
            "destination_key": pd.Categorical(rng.choice(["koeln", "berlin", "OTHER"], n)),
            "stop_index": rng.integers(0, 20, n).astype("int16"),
            "hour_local": rng.integers(0, 24, n),
            "minute_of_day": rng.integers(0, 1440, n),
            "weekday": np.full(n, 5 if weekend else 2),
            "is_weekend": np.full(n, weekend),
            "month": np.full(n, 3),
            "is_public_holiday": np.zeros(n, dtype=bool),
        }
    )


def _p(n: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).random(n)


def test_identical_samples_no_drift() -> None:
    ref = _frame(4000, 1)
    result = compute_drift(ref, ref, _p(4000, 2), _p(4000, 2), CFG)
    assert result.drift_share == 0.0
    assert not any(c.drifted for c in result.columns)
    assert not result.prediction.drifted


def test_shifted_numeric_drifts() -> None:
    ref = _frame(4000, 1)
    cur = _frame(3000, 3)
    cur["minute_of_day"] = cur["minute_of_day"] + 300
    result = compute_drift(cur, ref, _p(3000, 4), _p(4000, 2), CFG)
    col = next(c for c in result.columns if c.column == "minute_of_day")
    expected = wasserstein_distance(cur["minute_of_day"], ref["minute_of_day"]) / np.std(
        ref["minute_of_day"]
    )
    assert col.method == "wasserstein_normed"
    assert col.score == pytest.approx(expected)
    assert col.drifted and col.counted


def test_categorical_score_is_jensen_shannon() -> None:
    ref = _frame(4000, 1)
    cur = _frame(3000, 3)
    cur["train_type"] = pd.Categorical(np.full(3000, "ICE"))
    result = compute_drift(cur, ref, _p(3000, 4), _p(4000, 2), CFG)
    col = next(c for c in result.columns if c.column == "train_type")
    levels = ["ICE", "RE", "S"]
    p = cur["train_type"].astype(str).value_counts(normalize=True).reindex(levels, fill_value=0)
    q = ref["train_type"].astype(str).value_counts(normalize=True).reindex(levels, fill_value=0)
    assert col.method == "jensen_shannon"
    assert col.score == pytest.approx(jensenshannon(p, q, base=2))
    assert col.drifted


def test_new_category_union() -> None:
    ref = _frame(4000, 1)
    cur = _frame(3000, 3)
    cur["destination_key"] = pd.Categorical(np.full(3000, "neu"))
    result = compute_drift(cur, ref, _p(3000, 4), _p(4000, 2), CFG)
    col = next(c for c in result.columns if c.column == "destination_key")
    assert np.isfinite(col.score)
    assert col.score == pytest.approx(1.0)


def test_calendar_never_counted() -> None:
    ref = _frame(4000, 1)
    cur = ref.copy()
    cur["month"] = 11
    result = compute_drift(cur, ref, _p(4000, 2), _p(4000, 2), CFG)
    month = next(c for c in result.columns if c.column == "month")
    assert month.drifted and not month.counted
    assert result.drift_share == 0.0
    assert {c.column for c in result.columns if not c.counted} == set(CALENDAR_COLUMNS)


def test_share_is_over_seven_content_columns() -> None:
    ref = _frame(4000, 1)
    cur = _frame(4000, 1)
    cur["minute_of_day"] = cur["minute_of_day"] + 300
    cur["train_type"] = pd.Categorical(np.full(4000, "S"))
    result = compute_drift(cur, ref, _p(4000, 2), _p(4000, 2), CFG)
    assert len(CONTENT_NUMERIC) + len(CONTENT_CATEGORICAL) == 7
    assert result.drift_share == pytest.approx(2 / 7)


def test_reference_filtered_by_day_type() -> None:
    ref = pd.concat([_frame(3000, 1), _frame(1000, 5, weekend=True)], ignore_index=True)
    cur = _frame(500, 6, weekend=True)
    ref_p = _p(4000, 2)
    result = compute_drift(cur, ref, _p(500, 7), ref_p, CFG)
    assert day_type(cur) == "weekend_or_holiday"
    assert result.day_type == "weekend_or_holiday"
    assert result.reference_rows == 1000
    assert result.current_rows == 500


def test_empty_day_type_reference_falls_back_to_all() -> None:
    ref = _frame(3000, 1)
    cur = _frame(500, 6, weekend=True)
    result = compute_drift(cur, ref, _p(500, 7), _p(3000, 2), CFG)
    assert result.reference_rows == 3000


def test_constant_reference_std_floor() -> None:
    ref = _frame(2000, 1)
    ref["stop_index"] = 3
    cur = _frame(2000, 3)
    result = compute_drift(cur, ref, _p(2000, 4), _p(2000, 2), CFG)
    col = next(c for c in result.columns if c.column == "stop_index")
    assert np.isfinite(col.score)
    assert col.score == pytest.approx(
        wasserstein_distance(cur["stop_index"], ref["stop_index"]) / 0.001
    )


def test_empty_current_raises() -> None:
    ref = _frame(100, 1)
    with pytest.raises(ValueError, match="no current rows"):
        compute_drift(ref.iloc[:0], ref, np.array([]), _p(100, 2), CFG)
