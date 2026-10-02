"""Training configuration (`configs/training.yaml`), validated with pydantic."""

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationError

from dbdelay.errors import ConfigError


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FeatureConfig(_Section):
    min_count: PositiveInt


class BaselineConfig(_Section):
    min_count: PositiveInt


class EvaluationConfig(_Section):
    ece_bins: int = Field(ge=2)
    slice_min_rows: PositiveInt


class TrainingConfig(_Section):
    window_months: PositiveInt
    end_date: date | None = None
    test_days: PositiveInt
    valid_days: PositiveInt
    exclude_data_gaps: bool
    features: FeatureConfig
    baseline: BaselineConfig
    evaluation: EvaluationConfig


def load_training_config(path: Path) -> TrainingConfig:
    """Load and validate the training config.

    Raises:
        ConfigError: if the file is missing, not YAML, or fails validation.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read training config {path.name}") from exc
    try:
        return TrainingConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(
            f"invalid training config {path.name}: {exc.error_count()} errors"
        ) from None
