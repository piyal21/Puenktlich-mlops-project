"""Synthetic HF-shaped and silver-shaped frames for unit and integration tests."""

import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from dbdelay.data.silver import RAW_COLUMNS, RAW_DTYPES, make_event_id

HF_FILE_COLUMNS = RAW_COLUMNS[:-2]  # without ingested_at, _file_month


def hf_row(**overrides: Any) -> dict[str, Any]:
    """A valid, on-time Frankfurt departure on 2026-03-10 08:15 local (07:15 UTC)."""
    row: dict[str, Any] = {
        "eva": "08000105",
        "train_number": "15675",
        "line_number": "RB61",
        "final_destination_station": "Dieburg",
        "delay_in_min": 0,
        "departure_is_canceled": False,
        "train_type": "RB",
        "id": "-3640391175194580346-2603100815-1",
        "departure_planned_time": datetime(2026, 3, 10, 8, 15),
        "departure_change_time": datetime(2026, 3, 10, 8, 15),
        "ingested_at": datetime(2026, 9, 26, 10, 0, tzinfo=UTC),
        "_file_month": "2026-03",
    }
    row.update(overrides)
    return row


def hf_frame(*rows: dict[str, Any]) -> pd.DataFrame:
    """Rows → frame with exactly the raw input dtypes ``conform_hf`` expects."""
    return pd.DataFrame(list(rows), columns=list(RAW_COLUMNS)).astype(RAW_DTYPES)


def hf_parquet_bytes(df: pd.DataFrame) -> bytes:
    """Serialize HF-file columns the way the real monthly files look (ns timestamps)."""
    table = pa.Table.from_pandas(df[list(HF_FILE_COLUMNS)], preserve_index=False)
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


def silver_frame(n: int = 3, day: str = "2026-03-10") -> pd.DataFrame:
    """``n`` valid on-time silver rows on ``day`` (UTC), distinct rides."""
    planned = [pd.Timestamp(f"{day} 07:00", tz="UTC") + timedelta(minutes=i) for i in range(n)]
    rides = [f"{100 + i}-2603100600" for i in range(n)]
    return pd.DataFrame(
        {
            "event_id": [
                make_event_id("8000105", r, p) for r, p in zip(rides, planned, strict=True)
            ],
            "eva": ["8000105"] * n,
            "station_name": ["Frankfurt (Main) Hbf"] * n,
            "ride_id": rides,
            "stop_index": pd.Series([1] * n, dtype="int16"),
            "train_type": ["RB"] * n,
            "train_number": ["1"] * n,
            "line_number": [None] * n,
            "final_destination": ["Dieburg"] * n,
            "planned_departure_utc": pd.Series(planned, dtype="datetime64[us, UTC]"),
            "changed_departure_utc": pd.Series(planned, dtype="datetime64[us, UTC]"),
            "delay_min": pd.Series([0] * n, dtype="Int16"),
            "is_cancelled": pd.Series([False] * n, dtype="bool"),
            "is_late": pd.Series([False] * n, dtype="boolean"),
            "source": ["hf"] * n,
            "ingested_at": pd.Series(
                [pd.Timestamp("2026-09-26 10:00", tz="UTC")] * n, dtype="datetime64[us, UTC]"
            ),
        }
    )


def read_parquet_bytes(data: bytes) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(data))
