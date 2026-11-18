"""Daily performance metrics on real outcomes, without scikit-learn (slim API image).

Same definitions as `dbdelay.training.evaluate` (log loss clipped at the same EPS, ECE with
equal-width bins), so daily numbers compare with the champion's test metrics.
"""

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict
from scipy.stats import rankdata

EPS = 1e-6  # = dbdelay.training.evaluate.EPS


class PerformanceMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    rows: int
    late_rate: float | None = None
    mean_p_late: float | None = None
    brier: float | None = None
    log_loss: float | None = None
    roc_auc: float | None = None
    ece: float | None = None


def _roc_auc(y: NDArray[np.bool_], p: NDArray[np.float64]) -> float | None:
    """Mann-Whitney U / (n_pos * n_neg) with average ranks for ties (= sklearn's ROC-AUC)."""
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = rankdata(p)
    return float((ranks[y].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def _ece(y: NDArray[np.bool_], p: NDArray[np.float64], bins: int) -> float:
    index = np.minimum((p * bins).astype(np.int64), bins - 1)
    counts = np.bincount(index, minlength=bins)
    pred_sum = np.bincount(index, weights=p, minlength=bins)
    obs_sum = np.bincount(index, weights=y.astype(float), minlength=bins)
    filled = counts > 0
    gaps = np.abs(pred_sum[filled] - obs_sum[filled]) / counts[filled]
    return float((counts[filled] / len(y) * gaps).sum())


def performance(y: NDArray[np.bool_], p: NDArray[np.float64], ece_bins: int) -> PerformanceMetrics:
    """Brier, log loss, ROC-AUC (None for one class), ECE, actual and predicted late rate."""
    y = np.asarray(y, dtype=bool)
    p = np.asarray(p, dtype=float)
    n = len(y)
    if n == 0:
        return PerformanceMetrics(rows=0)
    yf = y.astype(float)
    clipped = np.clip(p, EPS, 1 - EPS)
    return PerformanceMetrics(
        rows=n,
        late_rate=float(yf.mean()),
        mean_p_late=float(p.mean()),
        brier=float(np.mean((p - yf) ** 2)),
        log_loss=float(-np.mean(yf * np.log(clipped) + (1 - yf) * np.log(1 - clipped))),
        roc_auc=_roc_auc(y, p),
        ece=_ece(y, p, ece_bins),
    )
