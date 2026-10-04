"""Isotonic calibration fitted on valid; applied with numpy.interp (serving needs no sklearn)."""

from typing import Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from dbdelay.errors import DataValidationError


class IsotonicCalibrator(BaseModel):
    """`calibrator.json`: piecewise-linear map from raw score to calibrated ``p_late``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    method: Literal["isotonic"] = "isotonic"
    x: tuple[float, ...] = Field(min_length=1)
    y: tuple[float, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _monotone(self) -> "IsotonicCalibrator":
        x, y = np.asarray(self.x), np.asarray(self.y)
        if len(x) != len(y):
            raise ValueError("x and y must have the same length")
        if np.any(np.diff(x) <= 0):
            raise ValueError("x must be strictly increasing")
        if np.any(np.diff(y) < 0) or y.min() < 0 or y.max() > 1:
            raise ValueError("y must be non-decreasing within [0, 1]")
        return self

    @classmethod
    def fit(cls, raw: ArrayLike, labels: ArrayLike) -> "IsotonicCalibrator":
        """Fit on raw scores vs boolean labels.

        Raises:
            DataValidationError: if inputs are empty or of different length.
        """
        # Fitting only (training); serving loads calibrator.json without scikit-learn.
        from sklearn.isotonic import IsotonicRegression  # noqa: PLC0415

        scores = np.asarray(raw, dtype=float)
        targets = np.asarray(labels, dtype=float)
        if scores.size == 0 or scores.shape != targets.shape:
            raise DataValidationError("calibration needs one label per raw score")
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(scores, targets)
        return cls(
            x=tuple(float(v) for v in iso.X_thresholds_),
            y=tuple(float(v) for v in iso.y_thresholds_),
        )

    def apply(self, raw: ArrayLike) -> NDArray[np.float64]:
        """Calibrated probabilities; inputs outside the fitted range take the end values."""
        out = np.interp(np.asarray(raw, dtype=float), self.x, self.y)
        return np.asarray(np.clip(out, 0.0, 1.0), dtype=np.float64)

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, text: str | bytes) -> "IsotonicCalibrator":
        """Parse `calibrator.json`.

        Raises:
            DataValidationError: if the JSON does not match the calibrator model.
        """
        try:
            return cls.model_validate_json(text)
        except ValidationError as exc:
            raise DataValidationError(f"invalid calibrator: {exc.error_count()} errors") from None
