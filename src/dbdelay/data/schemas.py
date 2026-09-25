"""Silver data contract (docs/architecture.md §3.3, ADR 0001).

Both sources (HF history and live API) must produce exactly this schema. Producers build
the right dtypes themselves; validation never coerces, so a producer bug fails loudly.
Changing a column, dtype, rule or the late threshold is a contract change (rules.md §1).
"""

from typing import Annotated

import numpy as np
import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaError, SchemaErrors
from pandera.typing.pandas import Series

from dbdelay.errors import DataValidationError

SILVER_SCHEMA_VERSION = 1
LATE_THRESHOLD_MIN = 6
SOURCES = ("hf", "live")

UtcTimestamp = Annotated[pd.DatetimeTZDtype, "us", "UTC"]


class SilverDepartures(pa.DataFrameModel):
    """One row per departure event at a station."""

    # Patterns end in `\Z`, not `$`: pandera uses re.match, where `$` also accepts a trailing "\n".
    event_id: Series[str] = pa.Field(unique=True, str_matches=r"^[0-9a-f]{40}\Z")
    # API form: 7 digits, no leading zero (HF stores "08000105").
    eva: Series[str] = pa.Field(str_matches=r"^[1-9][0-9]{6}\Z")
    station_name: Series[str] = pa.Field(str_matches=r"^\S(.*\S)?\Z")
    # `s@id` without the trailing stop number: "<trip hash>-<YYMMDDHHmm of trip start>".
    ride_id: Series[str] = pa.Field(str_matches=r"^-?[0-9]+-[0-9]{10}\Z")
    stop_index: Series[np.int16] = pa.Field(ge=1)
    # Upper-cased category (see `is_upper_case`); real values include spaces/hyphens ("L-RB").
    train_type: Series[str] = pa.Field(str_matches=r"^\S(.*\S)?\Z")
    train_number: Series[str] = pa.Field(nullable=True)
    line_number: Series[str] = pa.Field(nullable=True)
    final_destination: Series[str] = pa.Field(nullable=True)
    planned_departure_utc: Series[UtcTimestamp]
    changed_departure_utc: Series[UtcTimestamp] = pa.Field(nullable=True)
    delay_min: Series[pd.Int16Dtype] = pa.Field(nullable=True)
    is_cancelled: Series[bool]
    is_late: Series[pd.BooleanDtype] = pa.Field(nullable=True)
    source: Series[str] = pa.Field(isin=SOURCES)
    ingested_at: Series[UtcTimestamp]

    class Config:
        strict = True
        coerce = False

    @pa.check("train_type")
    @classmethod
    def is_upper_case(cls, train_type: "pd.Series[str]") -> "pd.Series[bool]":
        """Unicode-aware, so a non-ASCII lowercase letter ("ü") is rejected too."""
        return train_type == train_type.str.upper()

    @pa.dataframe_check
    @classmethod
    def cancelled_has_no_delay_or_label(cls, df: pd.DataFrame) -> "pd.Series[bool]":
        """Cancelled departures are excluded from the label (ADR 0001)."""
        return ~df["is_cancelled"] | (df["delay_min"].isna() & df["is_late"].isna())

    @pa.dataframe_check
    @classmethod
    def label_matches_delay(cls, df: pd.DataFrame) -> "pd.Series[bool]":
        """Not cancelled → delay known and `is_late == delay_min >= LATE_THRESHOLD_MIN`."""
        expected = df["delay_min"] >= LATE_THRESHOLD_MIN
        consistent = df["delay_min"].notna() & df["is_late"].notna() & (df["is_late"] == expected)
        return df["is_cancelled"] | consistent.fillna(False).astype(bool)

    @pa.dataframe_check
    @classmethod
    def delay_matches_times(cls, df: pd.DataFrame) -> "pd.Series[bool]":
        """Not cancelled → changed time set and `delay_min == changed - planned` in minutes.

        Both sources have minute resolution; "no change reported" means changed == planned.
        """
        delay = pd.to_timedelta(df["delay_min"].astype("Float64"), unit="min")
        expected = df["planned_departure_utc"] + delay
        consistent = df["changed_departure_utc"].notna() & (df["changed_departure_utc"] == expected)
        return df["is_cancelled"] | consistent.fillna(False).astype(bool)


_COLUMN_PRESENCE_CHECKS = {"column_in_dataframe", "column_in_schema"}


def _describe(failure_cases: pd.DataFrame) -> str:
    """Summarize failed checks as `column: check` — never row values."""
    problems: list[str] = []
    for context, column, check, case in failure_cases[
        ["schema_context", "column", "check", "failure_case"]
    ].itertuples(index=False):
        if context != "DataFrameSchema":
            problem = f"{column}: {check}"
        elif check in _COLUMN_PRESENCE_CHECKS:
            problem = f"{case}: {check}"  # the failure case is the column name
        else:
            problem = str(check)  # row-level check, reported once
        if problem not in problems:
            problems.append(problem)
    return "; ".join(problems)


def validate_silver(df: pd.DataFrame) -> pd.DataFrame:
    """Validate a silver frame against the contract.

    Args:
        df: Candidate silver rows.

    Returns:
        The same frame, unchanged.

    Raises:
        DataValidationError: listing each failed column/check (never row values).
    """
    try:
        return SilverDepartures.validate(df, lazy=True)
    except SchemaErrors as exc:
        raise DataValidationError(
            f"Silver contract violated: {_describe(exc.failure_cases)}"
        ) from None
    except SchemaError as exc:  # pragma: no cover - lazy=True reports via SchemaErrors
        raise DataValidationError(f"Silver contract violated: {exc.reason_code}") from None
