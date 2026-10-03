"""Training configuration (`configs/training.yaml`), validated with pydantic."""

from datetime import date
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationError, field_validator

from dbdelay.errors import ConfigError
from dbdelay.features.spec import RiskThresholds

# Set by train.py itself (determinism, grid) — not allowed in `lightgbm.params`.
MANAGED_PARAMS = frozenset(
    {
        "seed",
        "deterministic",
        "force_row_wise",
        "num_threads",
        "verbose",
        "metric",
        "num_leaves",
        "learning_rate",
        "min_data_in_leaf",
        "num_iterations",
        "num_boost_round",
    }
)
Rate = Annotated[float, Field(gt=0, le=1)]
LightGBMParam = str | int | float | bool


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FeatureConfig(_Section):
    min_count: PositiveInt


class BaselineConfig(_Section):
    min_count: PositiveInt


class EvaluationConfig(_Section):
    ece_bins: int = Field(ge=2)
    slice_min_rows: PositiveInt


class LightGBMGrid(_Section):
    num_leaves: tuple[Annotated[int, Field(ge=2)], ...] = Field(min_length=1)
    learning_rate: tuple[Rate, ...] = Field(min_length=1)
    min_data_in_leaf: tuple[PositiveInt, ...] = Field(min_length=1)


class LightGBMConfig(_Section):
    num_threads: PositiveInt
    num_boost_round: PositiveInt
    early_stopping_rounds: PositiveInt
    params: dict[str, LightGBMParam]
    grid: LightGBMGrid

    @field_validator("params")
    @classmethod
    def _check_params(cls, params: dict[str, LightGBMParam]) -> dict[str, LightGBMParam]:
        managed = sorted(MANAGED_PARAMS & set(params))
        if managed:
            raise ValueError(f"lightgbm.params must not set {managed}")
        if params.get("objective") != "binary":
            raise ValueError("lightgbm.params.objective must be 'binary'")
        return params


class GateConfig(_Section):
    min_brier_improvement_vs_baseline: float = Field(ge=0, lt=1)
    max_brier_regression_vs_champion: float = Field(ge=0)
    max_auc_drop_vs_champion: float = Field(ge=0)
    max_slice_auc_drop: float = Field(ge=0)
    min_test_rows: PositiveInt


class RegistryConfig(_Section):
    # pydantic patterns use Rust regex: `$` is end of text (no trailing-newline match).
    model_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    experiment: str = Field(min_length=1)


class ReleaseConfig(_Section):
    reference_sample_rows: PositiveInt


class TrainingConfig(_Section):
    window_months: PositiveInt
    end_date: date | None = None
    test_days: PositiveInt
    valid_days: PositiveInt
    exclude_data_gaps: bool
    features: FeatureConfig
    baseline: BaselineConfig
    evaluation: EvaluationConfig
    seed: int = Field(ge=0)
    lightgbm: LightGBMConfig
    risk_thresholds: RiskThresholds
    gate: GateConfig
    registry: RegistryConfig
    release: ReleaseConfig


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
