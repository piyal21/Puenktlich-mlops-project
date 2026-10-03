"""Synthetic HF-shaped and silver-shaped frames for unit and integration tests."""

import io
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from dbdelay.data.quality import QualityReport, StationQuality
from dbdelay.data.silver import (
    RAW_COLUMNS,
    RAW_DTYPES,
    make_event_id,
    quality_key,
    write_silver_month,
)
from dbdelay.features.spec import RiskThresholds
from dbdelay.storage import ObjectStore
from dbdelay.training.config import (
    BaselineConfig,
    EvaluationConfig,
    FeatureConfig,
    GateConfig,
    LightGBMConfig,
    LightGBMGrid,
    RegistryConfig,
    ReleaseConfig,
    TrainingConfig,
)

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


def quality_report(
    month: str,
    *,
    low_volume_hours: Sequence[str] = (),
    drop_days: Mapping[str, Sequence[str]] | None = None,
) -> QualityReport:
    """A minimal monthly quality report with the given gap flags."""
    stations = [
        StationQuality(eva=eva, rows=0, volume_drop_days=list(days))
        for eva, days in (drop_days or {}).items()
    ]
    return QualityReport(
        month=month,
        rows_read=0,
        rows_out=0,
        drops={},
        quarantined={},
        null_rates={},
        late_rate=None,
        cancelled_rate=None,
        stations=stations,
        low_volume_hours=list(low_volume_hours),
        edge_complete={"prev": True, "next": True},
        content_hash="0" * 64,
    )


def silver_day(  # noqa: PLR0913 - test builder with independent knobs
    day: str,
    n: int = 4,
    *,
    eva: str = "8000105",
    hour_utc: int = 7,
    late_every: int = 2,
    cancelled: int = 0,
) -> pd.DataFrame:
    """``n`` silver rows on UTC ``day`` from ``hour_utc``:00, one per minute.

    Every ``late_every``-th row is 10 min late; the last ``cancelled`` rows are cancelled.
    """
    frame = silver_frame(n, day)
    planned = [
        pd.Timestamp(f"{day} {hour_utc:02d}:00", tz="UTC") + timedelta(minutes=i) for i in range(n)
    ]
    rides = [f"{100 + i}-{day[2:4]}{day[5:7]}{day[8:10]}0600" for i in range(n)]
    late = [i % late_every == late_every - 1 for i in range(n)]
    is_cancelled = [i >= n - cancelled for i in range(n)]
    frame["eva"] = eva
    frame["ride_id"] = rides
    frame["event_id"] = [make_event_id(eva, r, p) for r, p in zip(rides, planned, strict=True)]
    frame["planned_departure_utc"] = pd.Series(planned, dtype="datetime64[us, UTC]")
    frame["changed_departure_utc"] = pd.Series(
        [
            None if c else p + timedelta(minutes=10 if lt else 0)
            for p, lt, c in zip(planned, late, is_cancelled, strict=True)
        ],
        dtype="datetime64[us, UTC]",
    )
    frame["delay_min"] = pd.Series(
        [None if c else (10 if lt else 0) for lt, c in zip(late, is_cancelled, strict=True)],
        dtype="Int16",
    )
    frame["is_cancelled"] = pd.Series(is_cancelled, dtype="bool")
    frame["is_late"] = pd.Series(
        [None if c else lt for lt, c in zip(late, is_cancelled, strict=True)], dtype="boolean"
    )
    return frame


PHASE4_SECTIONS: dict[str, Any] = {
    "seed": 42,
    "lightgbm": LightGBMConfig(
        num_threads=2,
        num_boost_round=50,
        early_stopping_rounds=10,
        params={"objective": "binary"},
        grid=LightGBMGrid(num_leaves=(7,), learning_rate=(0.1,), min_data_in_leaf=(20,)),
    ),
    "risk_thresholds": RiskThresholds(medium=0.2, high=0.45),
    "gate": GateConfig(
        min_brier_improvement_vs_baseline=0.05,
        max_brier_regression_vs_champion=0.0,
        max_auc_drop_vs_champion=0.005,
        max_slice_auc_drop=0.02,
        min_test_rows=100,
    ),
    "registry": RegistryConfig(
        model_name="puenktlich-delay-test", experiment="puenktlich-delay-test"
    ),
    "release": ReleaseConfig(reference_sample_rows=500),
}

MARCH_CONFIG = TrainingConfig(
    window_months=1,
    end_date=date(2026, 3, 31),
    test_days=7,
    valid_days=7,
    exclude_data_gaps=True,
    features=FeatureConfig(min_count=1),
    baseline=BaselineConfig(min_count=2),
    evaluation=EvaluationConfig(ece_bins=10, slice_min_rows=1),
    **PHASE4_SECTIONS,
)


SIGNAL_STATIONS = {"8000105": "Frankfurt (Main) Hbf", "8000261": "München Hbf"}


def signal_day(day: str, n: int = 120, *, seed: int = 0) -> pd.DataFrame:
    """``n`` labelled silver rows on UTC ``day`` with a learnable late signal.

    P(late) = 0.1, +0.6 if ``stop_index`` >= 6, +0.25 if the UTC hour is >= 12. The baseline's
    groups (station/type/hour/weekday) cannot see ``stop_index``; a 1-split stump sees only it.
    """
    rng = np.random.default_rng([seed, int(day.replace("-", ""))])
    frame = silver_frame(n, day)
    minutes = np.sort(rng.integers(0, 24 * 60, n))
    planned = [pd.Timestamp(f"{day} 00:00", tz="UTC") + timedelta(minutes=int(m)) for m in minutes]
    stop = rng.integers(1, 11, n)
    hours = np.array([p.hour for p in planned])
    late = rng.random(n) < 0.1 + np.where(stop >= 6, 0.6, 0.0) + np.where(hours >= 12, 0.25, 0.0)
    evas = np.array(list(SIGNAL_STATIONS))[rng.integers(0, 2, n)]
    rides = [f"{1000 + i}-{day[2:4]}{day[5:7]}{day[8:10]}0000" for i in range(n)]
    frame["eva"] = evas
    frame["station_name"] = [SIGNAL_STATIONS[e] for e in evas]
    frame["train_type"] = np.where(rng.random(n) < 0.5, "RE", "ICE")
    frame["ride_id"] = rides
    frame["stop_index"] = pd.Series(stop, dtype="int16")
    frame["event_id"] = [
        make_event_id(e, r, p) for e, r, p in zip(evas, rides, planned, strict=True)
    ]
    frame["planned_departure_utc"] = pd.Series(planned, dtype="datetime64[us, UTC]")
    frame["changed_departure_utc"] = pd.Series(
        [p + timedelta(minutes=10 if lt else 0) for p, lt in zip(planned, late, strict=True)],
        dtype="datetime64[us, UTC]",
    )
    frame["delay_min"] = pd.Series(np.where(late, 10, 0), dtype="Int16")
    frame["is_late"] = pd.Series(late, dtype="boolean")
    return frame


def put_signal_silver(store: ObjectStore, root: str = "", n: int = 120) -> None:
    """March 2026 of ``signal_day`` rows plus a gap-free quality report."""
    days = [signal_day(f"2026-03-{d:02d}", n) for d in range(1, 32)]
    write_silver_month(pd.concat(days, ignore_index=True), store, "2026-03", root)
    report = quality_report("2026-03")
    store.put_bytes(quality_key("2026-03", root), report.model_dump_json().encode())


def signal_frames(n: int = 120) -> dict[str, pd.DataFrame]:
    """Signal silver rows of March 2026 split like ``SIGNAL_CONFIG`` (no storage)."""
    rows = pd.concat([signal_day(f"2026-03-{d:02d}", n) for d in range(1, 32)], ignore_index=True)
    day = rows["planned_departure_utc"].dt.day
    return {
        "train": rows[day <= 17].reset_index(drop=True),
        "valid": rows[(day >= 18) & (day <= 24)].reset_index(drop=True),
        "test": rows[day >= 25].reset_index(drop=True),
    }


def put_march_silver(store: ObjectStore, root: str = "", *, with_report: bool = True) -> None:
    """March 2026: 4 rows/day at 07:00 UTC, every 2nd late; 1 cancelled on 03-05;
    gap hour 2026-03-16T08 (local) and station gap day 8000105 / 2026-03-20.

    With ``MARCH_CONFIG``: train 03-01..17 → 63 rows (31 late), valid 03-18..24 → 24 (12),
    test 03-25..31 → 28 (14); excluded: 1 cancelled, 4 gap_hours, 4 gap_station_days.
    """
    days = [silver_day(f"2026-03-{d:02d}", cancelled=1 if d == 5 else 0) for d in range(1, 32)]
    write_silver_month(pd.concat(days, ignore_index=True), store, "2026-03", root)
    if with_report:
        report = quality_report(
            "2026-03", low_volume_hours=["2026-03-16T08"], drop_days={"8000105": ["2026-03-20"]}
        )
        store.put_bytes(quality_key("2026-03", root), report.model_dump_json().encode())


SIGNAL_CONFIG = MARCH_CONFIG.model_copy(
    update={
        "baseline": BaselineConfig(min_count=5),
        "evaluation": EvaluationConfig(ece_bins=10, slice_min_rows=50),
    }
)
# One stump: learns stop_index only -> beats the baseline, loses to SIGNAL_CONFIG's model.
WEAK_LIGHTGBM = SIGNAL_CONFIG.lightgbm.model_copy(
    update={
        "num_boost_round": 1,
        "grid": LightGBMGrid(num_leaves=(2,), learning_rate=(0.1,), min_data_in_leaf=(20,)),
    }
)
