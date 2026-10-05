"""Evaluation report models (`metrics.json`); no scikit-learn so serving can read them."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, model_validator


class Metrics(BaseModel):
    n: int
    base_rate: float | None
    brier: float | None = None
    roc_auc: float | None = None
    pr_auc: float | None = None
    log_loss: float | None = None
    ece: float | None = None


class CalibrationBin(BaseModel):
    lower: float
    upper: float
    mean_pred: float
    observed_rate: float
    count: int


class SplitReport(BaseModel):
    overall: Metrics
    calibration: list[CalibrationBin]
    slices: dict[str, dict[str, Metrics]]


class EvaluationReport(BaseModel):
    """`metrics.json`."""

    snapshot_id: str
    spec_hash: str
    config: dict[str, Any]
    splits: dict[str, SplitReport]


class TrainingReport(BaseModel):
    """`metrics.json` in a model bundle: challenger, baseline and champion on the same test rows."""

    snapshot_id: str
    spec_hash: str
    git_sha: str
    trained_at: datetime
    train_start: date
    train_end: date
    test_rows: int
    challenger_valid: SplitReport
    challenger_test: SplitReport
    baseline_test: SplitReport
    champion_version: str | None = None
    champion_test: SplitReport | None = None

    @model_validator(mode="after")
    def _champion_pair(self) -> "TrainingReport":
        if (self.champion_version is None) != (self.champion_test is None):
            raise ValueError("champion_version and champion_test come together")
        return self
