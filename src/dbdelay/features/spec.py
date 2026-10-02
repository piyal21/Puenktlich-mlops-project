"""Feature spec: frozen category levels and station states, fitted on the train split only."""

import hashlib
from collections.abc import Sequence

import pandas as pd
from pydantic import BaseModel, ConfigDict, ValidationError

from dbdelay.data.stations import Station
from dbdelay.errors import DataValidationError

FEATURE_VERSION = 1
OTHER = "OTHER"
_MISSING = "none"

REQUIRED_COLUMNS: tuple[str, ...] = (
    "eva",
    "train_type",
    "line_number",
    "final_destination",
    "stop_index",
    "planned_departure_utc",
)
# Outcome and bookkeeping columns: build_features never reads them (ADR 0001 leakage policy).
FORBIDDEN_COLUMNS: tuple[str, ...] = (
    "changed_departure_utc",
    "delay_min",
    "is_cancelled",
    "is_late",
    "event_id",
    "ride_id",
    "ingested_at",
    "source",
)
CATEGORICAL_FEATURES: tuple[str, ...] = ("eva", "train_type", "line_key", "destination_key")
FEATURE_COLUMNS: tuple[str, ...] = (
    "eva",
    "train_type",
    "line_key",
    "destination_key",
    "stop_index",
    "hour_local",
    "minute_of_day",
    "weekday",
    "is_weekend",
    "month",
    "is_public_holiday",
)


class FeatureSpec(BaseModel):
    """Everything `build_features` needs besides the rows (saved as `feature_spec.json`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = FEATURE_VERSION
    features: tuple[str, ...] = FEATURE_COLUMNS
    min_count: int
    levels: dict[str, tuple[str, ...]]
    station_states: dict[str, str]

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @property
    def spec_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    @classmethod
    def from_json(cls, text: str | bytes) -> "FeatureSpec":
        """Parse a saved spec.

        Raises:
            DataValidationError: if the JSON does not match the spec model.
        """
        try:
            return cls.model_validate_json(text)
        except ValidationError as exc:
            raise DataValidationError(f"invalid feature spec: {exc.error_count()} errors") from None


def require_columns(df: pd.DataFrame) -> None:
    """Raise ``DataValidationError`` if an input column is missing or times are naive."""
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise DataValidationError(f"feature input is missing columns: {missing}")
    if not isinstance(df["planned_departure_utc"].dtype, pd.DatetimeTZDtype):
        raise DataValidationError("planned_departure_utc must be timezone-aware (UTC)")


def category_keys(df: pd.DataFrame) -> pd.DataFrame:
    """Raw string keys of the categorical features (before rare/unseen → OTHER)."""
    train_type = df["train_type"].fillna(_MISSING).astype(str)
    line = df["line_number"].fillna(_MISSING).astype(str)
    return pd.DataFrame(
        {
            "eva": df["eva"].fillna(_MISSING).astype(str),
            "train_type": train_type,
            "line_key": train_type + ":" + line,
            "destination_key": df["final_destination"].fillna(_MISSING).astype(str),
        },
        index=df.index,
    )


def fit_spec(train: pd.DataFrame, stations: Sequence[Station], min_count: int) -> FeatureSpec:
    """Freeze category levels (≥ ``min_count`` train rows, sorted, ``OTHER`` last).

    Raises:
        DataValidationError: if ``train`` is empty or misses input columns.
    """
    require_columns(train)
    if train.empty:
        raise DataValidationError("cannot fit a feature spec on an empty train split")
    keys = category_keys(train)
    levels: dict[str, tuple[str, ...]] = {}
    for column in CATEGORICAL_FEATURES:
        counts = keys[column].value_counts()
        kept = sorted(
            str(level) for level, n in counts.items() if n >= min_count and level != OTHER
        )
        levels[column] = (*kept, OTHER)
    return FeatureSpec(
        min_count=min_count,
        levels=levels,
        station_states={station.eva: station.state for station in stations},
    )
