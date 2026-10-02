import numpy as np
import pandas as pd
import pytest

from dbdelay.errors import DataValidationError
from dbdelay.training.baseline import BaselineModel

TRAIN = [
    # (eva, train_type, hour_local, weekday, late)
    ("A", "RB", 8, 1, True),
    ("A", "RB", 8, 1, True),
    ("A", "RB", 8, 2, False),
    ("A", "RB", 9, 3, False),
    ("B", "RB", 7, 0, False),
    ("B", "ICE", 7, 0, True),
    ("B", "ICE", 7, 0, True),
]


def _features(rows: list[tuple[str, str, int, int]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["eva", "train_type", "hour_local", "weekday"])


def _fit() -> BaselineModel:
    frame = _features([row[:4] for row in TRAIN])
    labels = np.array([row[4] for row in TRAIN])
    return BaselineModel.fit(frame, labels, min_count=2)


def test_each_backoff_level_is_used() -> None:
    queries = _features(
        [
            ("A", "RB", 8, 1),  # level 1: (A, RB, 8, 1) n=2 → 2/2
            ("A", "RB", 8, 5),  # level 2: (A, RB, 8) n=3 → 2/3
            ("A", "RB", 9, 3),  # level 3: (A, RB) n=4 → 2/4
            ("B", "RB", 7, 0),  # level 4: (RB) n=5 → 2/5
            ("C", "S", 7, 0),  # global: 4/7
        ]
    )
    assert _fit().predict(queries) == pytest.approx([1.0, 2 / 3, 0.5, 0.4, 4 / 7])


def test_json_round_trip_gives_identical_predictions() -> None:
    model = _fit()
    queries = _features([row[:4] for row in TRAIN] + [("C", "S", 7, 0)])
    again = BaselineModel.from_json(model.to_json())
    assert (again.min_count, again.global_rate) == (model.min_count, model.global_rate)
    np.testing.assert_array_equal(again.predict(queries), model.predict(queries))
    assert again.to_json() == model.to_json()


def test_fit_rejects_empty_or_mismatched_input() -> None:
    with pytest.raises(DataValidationError):
        BaselineModel.fit(_features([]), np.array([], dtype=bool), min_count=1)
    with pytest.raises(DataValidationError):
        BaselineModel.fit(_features([("A", "RB", 8, 1)]), np.array([True, False]), min_count=1)


def test_from_json_rejects_other_levels() -> None:
    with pytest.raises(DataValidationError):
        BaselineModel.from_json('{"min_count": 1, "global_rate": 0.5, "levels": []}')
    with pytest.raises(DataValidationError):
        BaselineModel.from_json("not json")
