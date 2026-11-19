"""Monitoring configuration (`configs/monitoring.yaml`), validated with pydantic."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationError

from dbdelay.errors import ConfigError

DEFAULT_PATH = Path("configs/monitoring.yaml")


class MonitoringConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    drift_threshold: float = Field(gt=0, le=1)
    drift_share_max: float = Field(ge=0, le=1)
    brier_ratio_max: float = Field(gt=0)
    consecutive_days: PositiveInt
    min_labeled_rows: PositiveInt
    ece_bins: PositiveInt
    history_max_days: PositiveInt


def load_monitoring_config(path: Path = DEFAULT_PATH) -> MonitoringConfig:
    """Load and validate the monitoring config.

    Raises:
        ConfigError: if the file is missing, not YAML, or fails validation.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read monitoring config {path.name}") from exc
    try:
        return MonitoringConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(
            f"invalid monitoring config {path.name}: {exc.error_count()} errors"
        ) from None
