"""`build_features`: the single place features are made (training, API, monitoring)."""

import pandas as pd

from dbdelay.features.calendar import is_public_holiday, local_parts
from dbdelay.features.spec import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    OTHER,
    FeatureSpec,
    category_keys,
    require_columns,
)


def build_features(df: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    """Model inputs for ``df`` in ``FEATURE_COLUMNS`` order, keeping ``df``'s index.

    Reads only ``REQUIRED_COLUMNS`` — never labels or outcome columns (leakage guard).
    Unseen category levels map to ``OTHER``. Values are assigned positionally, so
    duplicate or non-default indexes stay row-for-row.

    Raises:
        DataValidationError: if an input column is missing or times are not tz-aware.
    """
    require_columns(df)
    keys = category_keys(df)
    out = pd.DataFrame(index=df.index)
    for column in CATEGORICAL_FEATURES:
        levels = list(spec.levels[column])
        raw = pd.Series(keys[column].to_numpy())
        values = raw.where(raw.isin(levels), OTHER).to_numpy()
        out[column] = pd.Categorical(values, categories=levels)
    out["stop_index"] = df["stop_index"].to_numpy().astype("int16")
    planned = df["planned_departure_utc"]
    parts = local_parts(planned)
    for column in parts.columns:
        out[column] = parts[column].to_numpy()
    states = df["eva"].astype(str).map(spec.station_states)
    out["is_public_holiday"] = is_public_holiday(planned, states).to_numpy()
    return out[list(FEATURE_COLUMNS)]
