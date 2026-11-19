import numpy as np
import pytest
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from dbdelay.monitoring.performance import EPS, performance
from dbdelay.training.evaluate import expected_calibration_error


def _sample(n: int = 5000) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(42)
    p = rng.random(n)
    y = rng.random(n) < p
    p[:10] = np.round(p[:10], 1)  # some ties for the rank-based AUC
    return y, p


def test_metrics_match_sklearn() -> None:
    y, p = _sample()
    m = performance(y, p, ece_bins=10)
    assert m.rows == 5000
    assert m.brier == pytest.approx(brier_score_loss(y.astype(int), p), abs=1e-9)
    assert m.roc_auc == pytest.approx(roc_auc_score(y.astype(int), p), abs=1e-9)
    clipped = np.clip(p, EPS, 1 - EPS)
    assert m.log_loss == pytest.approx(log_loss(y.astype(int), clipped, labels=[0, 1]), abs=1e-9)
    assert m.ece == pytest.approx(expected_calibration_error(y, p, 10), abs=1e-12)
    assert m.late_rate == pytest.approx(y.mean())
    assert m.mean_p_late == pytest.approx(p.mean())


def test_auc_none_for_one_class() -> None:
    m = performance(np.zeros(100, dtype=bool), np.full(100, 0.2), ece_bins=10)
    assert m.roc_auc is None
    assert m.brier == pytest.approx(0.04)
    assert m.late_rate == 0.0


def test_empty_input_all_none_rows_zero() -> None:
    m = performance(np.array([], dtype=bool), np.array([], dtype=float), ece_bins=10)
    assert m.rows == 0
    assert m.model_dump(exclude={"rows"}) == dict.fromkeys(
        ("late_rate", "mean_p_late", "brier", "log_loss", "roc_auc", "ece")
    )


def test_log_loss_clips_probabilities() -> None:
    m = performance(np.array([True, False]), np.array([0.0, 1.0]), ece_bins=10)
    assert m.log_loss is not None
    assert np.isfinite(m.log_loss)
