import numpy as np
import pytest
from numpy.typing import NDArray
from sklearn.isotonic import IsotonicRegression

from dbdelay.errors import DataValidationError
from dbdelay.training.calibrate import IsotonicCalibrator


def _data(seed: int = 0) -> tuple[NDArray[np.float64], NDArray[np.bool_]]:
    rng = np.random.default_rng(seed)
    raw = rng.random(2000)
    labels = rng.random(2000) < raw**2
    return raw, labels


def test_apply_matches_sklearn_including_out_of_range() -> None:
    raw, labels = _data()
    calibrator = IsotonicCalibrator.fit(raw, labels)
    reference = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(raw, labels)
    probe = np.concatenate([np.random.default_rng(1).random(500), [-1.0, 0.0, 1.0, 2.0]])
    np.testing.assert_allclose(calibrator.apply(probe), reference.predict(probe), atol=1e-12)


def test_output_is_monotone_and_in_unit_interval() -> None:
    calibrator = IsotonicCalibrator.fit(*_data())
    out = calibrator.apply(np.linspace(-0.5, 1.5, 1001))
    assert np.all(np.diff(out) >= 0)
    assert out.min() >= 0.0
    assert out.max() <= 1.0


def test_json_round_trip() -> None:
    calibrator = IsotonicCalibrator.fit(*_data())
    assert IsotonicCalibrator.from_json(calibrator.to_json()) == calibrator
    assert '"method": "isotonic"' in calibrator.to_json()


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"schema_version": 1, "method": "isotonic", "x": [0.2, 0.1], "y": [0.1, 0.2]}',
        '{"schema_version": 1, "method": "isotonic", "x": [0.1, 0.2], "y": [0.3, 0.2]}',
        '{"schema_version": 1, "method": "isotonic", "x": [0.1], "y": [0.1, 0.2]}',
        '{"schema_version": 1, "method": "isotonic", "x": [0.1], "y": [1.5]}',
        '{"schema_version": 2, "method": "isotonic", "x": [0.1], "y": [0.5]}',
    ],
)
def test_bad_json_raises(text: str) -> None:
    with pytest.raises(DataValidationError):
        IsotonicCalibrator.from_json(text)


def test_fit_needs_matching_non_empty_inputs() -> None:
    with pytest.raises(DataValidationError):
        IsotonicCalibrator.fit(np.array([]), np.array([]))
    with pytest.raises(DataValidationError):
        IsotonicCalibrator.fit(np.array([0.1, 0.2]), np.array([True]))
