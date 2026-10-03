from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from numpy.typing import NDArray

from dbdelay.data.stations import load_stations
from dbdelay.errors import DataValidationError
from dbdelay.features.build import build_features
from dbdelay.features.spec import FEATURE_COLUMNS, fit_spec
from dbdelay.training.config import LightGBMConfig, LightGBMGrid
from dbdelay.training.train import (
    TrainResult,
    booster_from_text,
    feature_importance,
    predict_raw,
    train_lightgbm,
)
from tests.builders import SIGNAL_CONFIG, WEAK_LIGHTGBM, signal_frames

REPO = Path(__file__).resolve().parents[2]
STATIONS = load_stations(REPO / "configs" / "stations.yaml")
Data = dict[str, tuple[pd.DataFrame, NDArray[np.bool_]]]


@pytest.fixture(scope="module")
def data() -> Data:
    frames = signal_frames()
    spec = fit_spec(frames["train"], STATIONS, 1)
    return {
        name: (build_features(frame, spec), frame["is_late"].to_numpy(dtype=bool))
        for name, frame in frames.items()
    }


def _train(data: Data, cfg: LightGBMConfig = SIGNAL_CONFIG.lightgbm) -> TrainResult:
    (tx, ty), (vx, vy) = data["train"], data["valid"]
    return train_lightgbm(tx, ty, vx, vy, cfg, seed=42)


def test_training_is_deterministic(data: Data) -> None:
    assert _train(data).model_text() == _train(data).model_text()


def test_model_text_keeps_best_iteration_and_predicts_like_the_booster(data: Data) -> None:
    result = _train(data)
    test_x = data["test"][0]
    restored = booster_from_text(result.model_text())
    assert restored.num_trees() == result.best_iteration
    np.testing.assert_allclose(
        predict_raw(restored, test_x),
        result.booster.predict(test_x, num_iteration=result.best_iteration),
    )


def test_grid_picks_lowest_valid_logloss(data: Data) -> None:
    grid = LightGBMGrid(num_leaves=(2, 7), learning_rate=(0.1,), min_data_in_leaf=(20,))
    result = _train(data, SIGNAL_CONFIG.lightgbm.model_copy(update={"grid": grid}))
    assert len(result.grid) == 2
    best = min(result.grid, key=lambda s: s.valid_logloss)
    assert result.params["num_leaves"] == best.params["num_leaves"]
    assert result.valid_logloss == best.valid_logloss


def test_model_learns_the_signal(data: Data) -> None:
    booster = booster_from_text(_train(data).model_text())
    test_x, test_y = data["test"]
    p = predict_raw(booster, test_x)
    assert p[test_y].mean() > p[~test_y].mean() + 0.2
    gain = feature_importance(booster)["gain"]
    assert set(gain) == set(FEATURE_COLUMNS)
    assert max(gain, key=gain.__getitem__) == "stop_index"


def test_weak_config_trains_one_stump(data: Data) -> None:
    result = _train(data, WEAK_LIGHTGBM)
    assert booster_from_text(result.model_text()).num_trees() == 1


def test_one_class_train_labels_raise(data: Data) -> None:
    (tx, ty), (vx, vy) = data["train"], data["valid"]
    with pytest.raises(DataValidationError):
        train_lightgbm(tx, np.zeros_like(ty), vx, vy, SIGNAL_CONFIG.lightgbm, seed=42)
