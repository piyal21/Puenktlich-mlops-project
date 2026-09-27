"""HF → silver conform and silver partition writing (architecture §3.3, ADR 0001)."""

import hashlib
import io
from datetime import date

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from dbdelay.data.months import month_days
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
