"""Row-level quarantine and the per-month quality report (rules.md §5.3)."""

import hashlib
from collections.abc import Sequence
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

import pandas as pd
from pydantic import BaseModel

from dbdelay.data.months import month_days
from dbdelay.data.schemas import SILVER_SCHEMA_VERSION

if TYPE_CHECKING:  # silver imports this module at runtime
    from dbdelay.data.silver import ConformResult

# s@id: "<trip hash>-<YYMMDDHHmm>-<stop ≥ 1>"
_ID_PATTERN = r"-?[0-9]+-[0-9]{10}-[1-9][0-9]*"
QUARANTINE_REASONS: tuple[str, ...] = (
    "bad_id",
    "missing_train_type",
    "missing_cancel_flag",
    "missing_delay",
    "delay_mismatch",
)


def _as_bool(mask: "pd.Series[bool]") -> "pd.Series[bool]":
    return mask.fillna(False).astype(bool)


def split_quarantine(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split HF-shaped rows into (usable, quarantined + ``quarantine_reason``).

    Never modifies values: rows either pass as they are or are set aside with a reason.
    """
    ids = raw["id"].astype("string")
    train_type = raw["train_type"].astype("string").str.strip()
    cancelled = raw["departure_is_canceled"]
    not_cancelled = _as_bool(cancelled.eq(False))
    delay = raw["delay_in_min"]
    expected = raw["departure_planned_time"] + pd.to_timedelta(delay.astype("Float64"), unit="min")
    rules: dict[str, pd.Series] = {
        "bad_id": ~_as_bool(ids.str.fullmatch(_ID_PATTERN)),
        "missing_train_type": _as_bool(train_type.isna() | train_type.eq("")),
        "missing_cancel_flag": _as_bool(cancelled.isna()),
        "missing_delay": not_cancelled & _as_bool(delay.isna()),
        "delay_mismatch": not_cancelled & ~_as_bool(raw["departure_change_time"].eq(expected)),
    }
    reason = pd.Series(pd.NA, index=raw.index, dtype="object")
    for name in QUARANTINE_REASONS:
        reason = reason.mask(reason.isna() & rules[name], name)
    flagged = reason.notna()
    quarantined = raw[flagged].assign(quarantine_reason=reason[flagged].astype("object"))
    return raw[~flagged], quarantined


VOLUME_DROP_RATIO = 0.5  # a station-day below half the station's monthly median
LOW_HOUR_RATIO = 0.25  # an hour below a quarter of that hour-of-day's monthly median
DAY_HOURS = range(6, 22)  # 06:00-21:59 Europe/Berlin
_BERLIN = "Europe/Berlin"


class StationQuality(BaseModel):
    eva: str
    rows: int
    volume_drop_days: list[str]


class QualityReport(BaseModel):
    schema_version: int = SILVER_SCHEMA_VERSION
    source: Literal["hf"] = "hf"
    month: str
    rows_read: int
    rows_out: int
    drops: dict[str, int]
    quarantined: dict[str, int]
    null_rates: dict[str, float]
    late_rate: float | None
    cancelled_rate: float | None
    stations: list[StationQuality]
    low_volume_hours: list[str]
    edge_complete: dict[str, bool]
    content_hash: str


def _station_quality(
    silver: pd.DataFrame, month: str, station_evas: Sequence[str]
) -> list[StationQuality]:
    if silver.empty:
        return [StationQuality(eva=eva, rows=0, volume_drop_days=[]) for eva in station_evas]
    days = month_days(month)
    daily = (
        silver.groupby(["eva", silver["planned_departure_utc"].dt.date])
        .size()
        .unstack(fill_value=0)
        .reindex(index=list(station_evas), columns=days, fill_value=0)
    )
    result = []
    for eva, counts in daily.iterrows():
        median = float(counts.median())
        low_days = [
            str(day)  # datetime.date → ISO
            for day, n in counts.items()
            if median > 0 and n < VOLUME_DROP_RATIO * median
        ]
        result.append(
            StationQuality(eva=str(eva), rows=int(counts.sum()), volume_drop_days=low_days)
        )
    return result


def _low_volume_hours(silver: pd.DataFrame, month: str) -> list[str]:
    if silver.empty:
        return []
    days = month_days(month)
    hours = pd.date_range(
        pd.Timestamp(days[0]).tz_localize(_BERLIN),
        pd.Timestamp(days[-1]).tz_localize(_BERLIN) + timedelta(hours=23),
        freq="h",
    )
    hours = hours[hours.hour.isin(list(DAY_HOURS))]
    local = silver["planned_departure_utc"].dt.tz_convert(_BERLIN).dt.floor("h")
    counts = local.value_counts().reindex(hours, fill_value=0)
    median_by_hour = counts.groupby(counts.index.hour).transform("median")
    low = counts[(median_by_hour > 0) & (counts < LOW_HOUR_RATIO * median_by_hour)]
    return [f"{ts:%Y-%m-%dT%H}" for ts in low.index]


def content_hash(df: pd.DataFrame) -> str:
    """Order-independent SHA-256 of a silver frame's values (rows sorted by ``event_id``)."""
    ordered = df.sort_values("event_id").reset_index(drop=True)
    row_hashes = pd.util.hash_pandas_object(ordered, index=False).to_numpy()
    return hashlib.sha256(row_hashes.tobytes()).hexdigest()


def build_report(
    month: str,
    *,
    rows_read: int,
    conform: "ConformResult",
    edge_complete: dict[str, bool],
    station_evas: Sequence[str],
) -> QualityReport:
    """Summarise one built month (rules.md §5.3)."""
    silver, quarantine = conform.silver, conform.quarantine
    quarantined = dict.fromkeys(QUARANTINE_REASONS, 0)
    quarantined.update(
        {str(k): int(v) for k, v in quarantine["quarantine_reason"].value_counts().items()}
    )
    has_rows = not silver.empty
    labelled = silver["is_late"].dropna()
    return QualityReport(
        month=month,
        rows_read=rows_read,
        rows_out=len(silver),
        drops=conform.drops,
        quarantined=quarantined,
        null_rates={c: float(silver[c].isna().mean()) if has_rows else 0.0 for c in silver.columns},
        late_rate=float(labelled.astype(bool).mean()) if len(labelled) else None,
        cancelled_rate=float(silver["is_cancelled"].mean()) if has_rows else None,
        stations=_station_quality(silver, month, station_evas),
        low_volume_hours=_low_volume_hours(silver, month),
        edge_complete=edge_complete,
        content_hash=content_hash(silver),
    )
