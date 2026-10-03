"""LightGBM training: small grid, early stopping on valid, native categoricals (arch. §5.2)."""

import itertools
from dataclasses import dataclass
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pydantic import BaseModel

from dbdelay.errors import DataValidationError
from dbdelay.features.spec import CATEGORICAL_FEATURES
from dbdelay.training.config import LightGBMConfig

GRID_KEYS: tuple[str, ...] = ("num_leaves", "learning_rate", "min_data_in_leaf")
METRIC = "binary_logloss"


class GridScore(BaseModel):
    params: dict[str, float | int]
    best_iteration: int
    valid_logloss: float


@dataclass(frozen=True)
class TrainResult:
    booster: lgb.Booster
    params: dict[str, Any]
    best_iteration: int
    grid: list[GridScore]

    @property
    def valid_logloss(self) -> float:
        """Valid log loss of the chosen grid point (the lowest of the grid)."""
        return min(score.valid_logloss for score in self.grid)

    def model_text(self) -> str:
        """`model.txt`: the booster cut at the best iteration (LightGBM text format, no pickle)."""
        return str(self.booster.model_to_string(num_iteration=self.best_iteration))


def base_params(cfg: LightGBMConfig, seed: int) -> dict[str, Any]:
    """Params shared by every grid point; fixed threads + deterministic for reproducibility."""
    return {
        **cfg.params,
        "metric": METRIC,
        "seed": seed,
        "deterministic": True,
        "force_row_wise": True,
        "num_threads": cfg.num_threads,
        "verbose": -1,
    }


def grid_points(cfg: LightGBMConfig) -> list[dict[str, float | int]]:
    grid = cfg.grid
    return [
        dict(zip(GRID_KEYS, values, strict=True))
        for values in itertools.product(grid.num_leaves, grid.learning_rate, grid.min_data_in_leaf)
    ]


def _dataset(
    features: pd.DataFrame, labels: NDArray[np.bool_], reference: lgb.Dataset | None = None
) -> lgb.Dataset:
    return lgb.Dataset(
        features,
        label=labels.astype(np.int8),
        categorical_feature=list(CATEGORICAL_FEATURES),
        reference=reference,
        free_raw_data=False,
    )


def _check_labels(name: str, features: pd.DataFrame, labels: NDArray[np.bool_]) -> None:
    if features.empty or len(features) != len(labels):
        raise DataValidationError(f"{name} needs rows and one label per row")
    if labels.all() or not labels.any():
        raise DataValidationError(f"{name} labels need both classes")


def train_lightgbm(  # noqa: PLR0913 - two splits x (features, labels) + config + seed
    train_x: pd.DataFrame,
    train_y: NDArray[np.bool_],
    valid_x: pd.DataFrame,
    valid_y: NDArray[np.bool_],
    cfg: LightGBMConfig,
    *,
    seed: int,
) -> TrainResult:
    """Train one booster per grid point; keep the one with the lowest valid log loss.

    A fresh Dataset per grid point: `min_data_in_leaf` affects Dataset construction.
    Ties keep the earlier grid point.

    Raises:
        DataValidationError: empty split, label/row mismatch, or a single label class.
    """
    train_labels = np.asarray(train_y, dtype=bool)
    valid_labels = np.asarray(valid_y, dtype=bool)
    _check_labels("train", train_x, train_labels)
    _check_labels("valid", valid_x, valid_labels)
    scores: list[GridScore] = []
    best: tuple[GridScore, lgb.Booster, dict[str, Any]] | None = None
    for point in grid_points(cfg):
        params = {**base_params(cfg, seed), **point}
        train_set = _dataset(train_x, train_labels)
        valid_set = _dataset(valid_x, valid_labels, reference=train_set)
        booster = lgb.train(
            params,
            train_set,
            num_boost_round=cfg.num_boost_round,
            valid_sets=[valid_set],
            valid_names=["valid"],
            callbacks=[lgb.early_stopping(cfg.early_stopping_rounds, verbose=False)],
        )
        score = GridScore(
            params=point,
            best_iteration=int(booster.best_iteration) or int(booster.current_iteration()),
            valid_logloss=float(booster.best_score["valid"][METRIC]),
        )
        scores.append(score)
        if best is None or score.valid_logloss < best[0].valid_logloss:
            best = (score, booster, params)
    if best is None:  # pragma: no cover - config validation guarantees a non-empty grid
        raise DataValidationError("empty LightGBM grid")
    score, booster, params = best
    return TrainResult(
        booster=booster, params=params, best_iteration=score.best_iteration, grid=scores
    )


def booster_from_text(text: str) -> lgb.Booster:
    return lgb.Booster(model_str=text)


def predict_raw(booster: lgb.Booster, features: pd.DataFrame) -> NDArray[np.float64]:
    """Uncalibrated probabilities (all trees of the booster)."""
    return np.asarray(booster.predict(features), dtype=np.float64)


def feature_importance(booster: lgb.Booster) -> dict[str, dict[str, float]]:
    """Gain and split importance per feature name."""
    names = [str(name) for name in booster.feature_name()]
    return {
        kind: dict(zip(names, (float(v) for v in booster.feature_importance(kind)), strict=True))
        for kind in ("gain", "split")
    }
