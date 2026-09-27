"""HF → silver conform and silver partition writing (architecture §3.3, ADR 0001)."""

import hashlib
import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from dbdelay.data.months import month_bounds_utc, month_days
from dbdelay.data.quality import split_quarantine
from dbdelay.data.schemas import LATE_THRESHOLD_MIN, validate_silver
from dbdelay.data.stations import Station, alias_map
from dbdelay.errors import DataValidationError
from dbdelay.storage import ObjectStore

BERLIN = "Europe/Berlin"

# Raw input: HF file columns (as read by DuckDB) + provenance added by hf_backfill.
RAW_COLUMNS: tuple[str, ...] = (
    "eva",
    "train_number",
    "line_number",
    "final_destination_station",
    "delay_in_min",
    "departure_is_canceled",
    "train_type",
    "id",
    "departure_planned_time",
    "departure_change_time",
    "ingested_at",
    "_file_month",
)
RAW_DTYPES: dict[str, str] = {
    "eva": "object",
    "train_number": "object",
    "line_number": "object",
    "final_destination_station": "object",
    "delay_in_min": "Int32",
    "departure_is_canceled": "boolean",
    "train_type": "object",
    "id": "object",
    "departure_planned_time": "datetime64[ns]",
    "departure_change_time": "datetime64[ns]",
    "ingested_at": "datetime64[us, UTC]",
    "_file_month": "object",
}
SILVER_COLUMNS: tuple[str, ...] = (
    "event_id",
    "eva",
    "station_name",
    "ride_id",
    "stop_index",
    "train_type",
    "train_number",
    "line_number",
    "final_destination",
    "planned_departure_utc",
    "changed_departure_utc",
    "delay_min",
    "is_cancelled",
    "is_late",
    "source",
    "ingested_at",
)
_TS = pa.timestamp("us", tz="UTC")
SILVER_ARROW_SCHEMA = pa.schema(
    [
        ("event_id", pa.string()),
        ("eva", pa.string()),
        ("station_name", pa.string()),
        ("ride_id", pa.string()),
        ("stop_index", pa.int16()),
        ("train_type", pa.string()),
        ("train_number", pa.string()),
        ("line_number", pa.string()),
        ("final_destination", pa.string()),
        ("planned_departure_utc", _TS),
        ("changed_departure_utc", _TS),
        ("delay_min", pa.int16()),
        ("is_cancelled", pa.bool_()),
        ("is_late", pa.bool_()),
        ("source", pa.string()),
        ("ingested_at", _TS),
    ]
)


def make_event_id(eva: str, ride_id: str, planned_utc: pd.Timestamp) -> str:
    """Primary key: SHA-1 hex of ``eva|ride_id|YYYY-MM-DDTHH:MMZ`` (architecture §3.3).

    Raises:
        ValueError: if ``planned_utc`` is not a UTC timestamp.
    """
    if planned_utc.tzinfo is None or planned_utc.utcoffset() != pd.Timedelta(0):
        raise ValueError("planned_utc must be a UTC timestamp")
    key = f"{eva}|{ride_id}|{planned_utc:%Y-%m-%dT%H:%M}Z"
    return hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()


def silver_key(day: date, root: str = "") -> str:
    return f"{root}silver/departures/source=hf/date={day:%Y-%m-%d}/part-0.parquet"


def quarantine_key(month: str, root: str = "") -> str:
    return f"{root}silver/_quarantine/source=hf/month={month}/part-0.parquet"


def quality_key(month: str, root: str = "") -> str:
    return f"{root}silver/_quality/source=hf/month={month}.json"


def to_parquet_bytes(df: pd.DataFrame, schema: pa.Schema | None = None) -> bytes:
    """Deterministic parquet bytes (no index, zstd)."""
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    return buffer.getvalue()


def write_silver_month(
    silver: pd.DataFrame, store: ObjectStore, month: str, root: str = ""
) -> dict[str, int]:
    """Overwrite one partition per day of ``month`` (empty days included).

    Returns:
        Rows written per ISO day.
    """
    days = silver["planned_departure_utc"].dt.date
    counts: dict[str, int] = {}
    for day in month_days(month):
        part = silver[days == day].sort_values("event_id").reset_index(drop=True)
        part = part[list(SILVER_COLUMNS)]
        store.put_bytes(silver_key(day, root), to_parquet_bytes(part, SILVER_ARROW_SCHEMA))
        counts[day.isoformat()] = len(part)
    return counts


def content_hash(df: pd.DataFrame) -> str:
    """Order-independent SHA-256 of a silver frame's values."""
    ordered = df[list(SILVER_COLUMNS)].sort_values("event_id").reset_index(drop=True)
    row_hashes = pd.util.hash_pandas_object(ordered, index=False).to_numpy()
    return hashlib.sha256(row_hashes.tobytes()).hexdigest()


DROP_REASONS: tuple[str, ...] = (
    "not_supported_station",
    "dst_ambiguous_or_nonexistent",
    "out_of_month",
    "duplicate",
)


@dataclass(frozen=True)
class ConformResult:
    """Silver rows, quarantined raw rows (+ ``quarantine_reason``) and drop counts."""

    silver: pd.DataFrame
    quarantine: pd.DataFrame
    drops: dict[str, int]


def _nullable_str(values: pd.Series) -> pd.Series:
    """Object column with ``None`` for missing values (the contract rejects NaN floats)."""
    result = values.astype("object")
    result[values.isna()] = None
    return result


def _in_month_local(raw: pd.DataFrame, month: str) -> "pd.Series[bool]":
    start = pd.Timestamp(f"{month}-01")
    end = start + pd.offsets.MonthBegin(1)
    planned = raw["departure_planned_time"]
    in_month = (planned >= start) & (planned < end)
    return (in_month | (planned.isna() & raw["_file_month"].eq(month))).astype(bool)


def conform_hf(raw: pd.DataFrame, stations: Sequence[Station], month: str) -> ConformResult:
    """Turn HF rows (``RAW_COLUMNS``) into silver rows for ``month`` (UTC dates).

    Raises:
        DataValidationError: if input columns are missing or the output breaks the contract.
    """
    missing = [c for c in RAW_COLUMNS if c not in raw.columns]
    if missing:
        raise DataValidationError(f"HF input missing columns: {', '.join(missing)}")
    drops = dict.fromkeys(DROP_REASONS, 0)
    names = {s.eva: s.name for s in stations}

    frame = raw.assign(eva=raw["eva"].astype("string").str.lstrip("0").map(alias_map(stations)))
    supported = frame["eva"].notna()
    drops["not_supported_station"] = int((~supported).sum())
    ok, quarantine = split_quarantine(frame[supported])

    planned = ok["departure_planned_time"].dt.tz_localize(
        BERLIN, ambiguous="NaT", nonexistent="NaT"
    )
    changed = ok["departure_change_time"].dt.tz_localize(BERLIN, ambiguous="NaT", nonexistent="NaT")
    dst_bad = planned.isna() | (ok["departure_change_time"].notna() & changed.isna())
    drops["dst_ambiguous_or_nonexistent"] = int(dst_bad.sum())
    ok, planned, changed = ok[~dst_bad], planned[~dst_bad], changed[~dst_bad]

    cancelled = ok["departure_is_canceled"].astype(bool)
    delay = ok["delay_in_min"].astype("Int16").mask(cancelled)
    planned_utc = planned.dt.tz_convert("UTC").dt.as_unit("us")
    changed_utc = changed.dt.tz_convert("UTC").dt.as_unit("us")

    # HF delays are naive local differences; across a DST switch they disagree with UTC.
    utc_expected = planned_utc + pd.to_timedelta(delay.astype("Float64"), unit="min")
    straddle = ~cancelled & ~changed_utc.eq(utc_expected).fillna(False).astype(bool)
    if straddle.any():  # concat with an empty frame trips a pandas/NumPy deprecation
        extra = ok[straddle].assign(quarantine_reason="delay_mismatch_utc")
        quarantine = extra if quarantine.empty else pd.concat([quarantine, extra])
    keep = ~straddle
    ok, delay = ok[keep], delay[keep]
    planned_utc, changed_utc = planned_utc[keep], changed_utc[keep]

    ride_id = ok["id"].str.replace(r"-[0-9]+$", "", regex=True)
    silver = pd.DataFrame(
        {
            "eva": ok["eva"].astype("object"),
            "station_name": ok["eva"].map(names).astype("object"),
            "ride_id": ride_id.astype("object"),
            "stop_index": ok["id"].str.extract(r"-([0-9]+)$", expand=False).astype("int16"),
            "train_type": ok["train_type"].str.strip().str.upper().astype("object"),
            "train_number": _nullable_str(ok["train_number"]),
            "line_number": _nullable_str(ok["line_number"]),
            "final_destination": _nullable_str(ok["final_destination_station"]),
            "planned_departure_utc": planned_utc,
            "changed_departure_utc": changed_utc,
            "delay_min": delay,
            "is_cancelled": ok["departure_is_canceled"].astype(bool),
            "is_late": (delay >= LATE_THRESHOLD_MIN).astype("boolean"),
            "source": "hf",
            "ingested_at": ok["ingested_at"],
            "_id": ok["id"],
        },
        index=ok.index,
    )

    start, end = month_bounds_utc(month)
    in_month = (silver["planned_departure_utc"] >= start) & (silver["planned_departure_utc"] < end)
    drops["out_of_month"] = int((~in_month).sum())
    silver = silver[in_month]

    event_ids = [
        make_event_id(e, r, p)
        for e, r, p in zip(
            silver["eva"], silver["ride_id"], silver["planned_departure_utc"], strict=True
        )
    ]
    # Explicit dtype: an empty list would otherwise become a float column.
    silver.insert(0, "event_id", pd.Series(event_ids, index=silver.index, dtype="object"))
    silver = silver.sort_values(
        ["event_id", "ingested_at", "_id"], ascending=[True, False, True], kind="stable"
    )
    duplicate = silver["event_id"].duplicated()
    drops["duplicate"] = int(duplicate.sum())
    silver = silver[~duplicate][list(SILVER_COLUMNS)].reset_index(drop=True)
    validate_silver(silver)

    quarantine = quarantine[_in_month_local(quarantine, month)].reset_index(drop=True)
    return ConformResult(silver=silver, quarantine=quarantine, drops=drops)
