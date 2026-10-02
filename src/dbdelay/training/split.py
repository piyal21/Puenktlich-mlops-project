"""Time-based train/valid/test snapshot of labelled silver rows (architecture §3.4, §5.3)."""

import hashlib
import io
from calendar import monthrange
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict

from dbdelay.data.quality import QualityReport
from dbdelay.data.silver import (
    SILVER_ARROW_SCHEMA,
    SILVER_COLUMNS,
    quality_key,
    silver_key,
    to_parquet_bytes,
)
from dbdelay.errors import DataValidationError, NotFoundError
from dbdelay.features.calendar import BERLIN
from dbdelay.storage import ObjectStore
from dbdelay.training.config import TrainingConfig

SPLITS: tuple[str, ...] = ("train", "valid", "test")
SILVER_PREFIX = "silver/departures/source=hf/"
SNAPSHOT_FILE = "snapshot.json"
_SPLIT_CONFIG_KEYS = ("window_months", "end_date", "test_days", "valid_days", "exclude_data_gaps")


class Windows(BaseModel):
    """Inclusive UTC-date bounds of the snapshot window and its splits."""

    model_config = ConfigDict(frozen=True)

    start: date
    valid_start: date
    test_start: date
    end: date

    def split_ranges(self) -> dict[str, tuple[date, date]]:
        one = timedelta(days=1)
        return {
            "train": (self.start, self.valid_start - one),
            "valid": (self.valid_start, self.test_start - one),
            "test": (self.test_start, self.end),
        }

    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]

    def months(self) -> list[str]:
        return sorted({f"{day:%Y-%m}" for day in self.days()})


def _minus_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def compute_windows(end: date, cfg: TrainingConfig) -> Windows:
    """Window = the ``window_months`` ending on ``end``; test/valid are its last days.

    Raises:
        DataValidationError: if no train day would be left.
    """
    start = _minus_months(end, cfg.window_months) + timedelta(days=1)
    test_start = end - timedelta(days=cfg.test_days - 1)
    valid_start = test_start - timedelta(days=cfg.valid_days)
    if valid_start <= start:
        raise DataValidationError("window is too short for the valid and test splits")
    return Windows(start=start, valid_start=valid_start, test_start=test_start, end=end)


def gap_masks(
    df: pd.DataFrame, reports: Sequence[QualityReport]
) -> tuple["pd.Series[bool]", "pd.Series[bool]"]:
    """Rows in a flagged low-volume local hour, and rows on a station's flagged UTC day."""
    planned = df["planned_departure_utc"]
    gap_hours = pd.DatetimeIndex(
        [
            pd.Timestamp(f"{hour}:00").tz_localize(BERLIN)
            for report in reports
            for hour in report.low_volume_hours
        ],
        tz=BERLIN,
    )
    # Berlin offsets are whole hours, so flooring in UTC equals flooring in local time.
    local_hour = planned.dt.floor("h").dt.tz_convert(BERLIN)
    in_hour = local_hour.isin(gap_hours).to_numpy()

    drop_days: dict[str, set[str]] = {}
    for report in reports:
        for station in report.stations:
            drop_days.setdefault(station.eva, set()).update(station.volume_drop_days)
    utc_day = planned.dt.floor("D")
    evas = df["eva"].astype(str).to_numpy()
    in_day = np.zeros(len(df), dtype=bool)
    for eva, days in sorted(drop_days.items()):
        flagged = pd.DatetimeIndex(sorted(days)).tz_localize("UTC")
        in_day |= (evas == eva) & utc_day.isin(flagged).to_numpy()
    return (
        pd.Series(in_hour, index=df.index, dtype="bool"),
        pd.Series(in_day, index=df.index, dtype="bool"),
    )


def assign_split(planned_utc: pd.Series, windows: Windows) -> pd.Series:
    """``train`` / ``valid`` / ``test`` by the UTC date of the planned departure."""
    day = planned_utc.dt.floor("D")
    valid_start = pd.Timestamp(windows.valid_start.isoformat(), tz="UTC")
    test_start = pd.Timestamp(windows.test_start.isoformat(), tz="UTC")
    labels = np.where(day < valid_start, "train", np.where(day < test_start, "valid", "test"))
    return pd.Series(labels, index=planned_utc.index)


class SplitInfo(BaseModel):
    start: date
    end: date
    rows: int
    late_rate: float


class SnapshotManifest(BaseModel):
    """`snapshot.json` — what the snapshot contains and how it was made."""

    snapshot_id: str
    content_hash: str
    window_start: date
    window_end: date
    splits: dict[str, SplitInfo]
    excluded: dict[str, int]
    silver_days: int
    quality_months: list[str]
    config: dict[str, Any]


def snapshot_prefix(snapshot_id: str, root: str = "") -> str:
    return f"{root}gold/training_sets/{snapshot_id}/"


def latest_silver_day(store: ObjectStore, root: str = "") -> date:
    """Newest UTC day whose silver partition has rows.

    Built months contain an (empty) partition for every day, so the newest key alone
    would point past the data of a partially collected month.

    Raises:
        DataValidationError: if there is no silver row at all.
    """
    days = sorted(
        {
            date.fromisoformat(key.split("date=", 1)[1][:10])
            for key in store.iter_keys(root + SILVER_PREFIX)
            if "date=" in key
        },
        reverse=True,
    )
    for day in days:
        footer = pq.read_metadata(io.BytesIO(store.get_bytes(silver_key(day, root))))
        if footer.num_rows > 0:
            return day
    raise DataValidationError("no silver rows found")


def _load_reports(store: ObjectStore, months: Sequence[str], root: str) -> list[QualityReport]:
    reports = []
    for month in months:
        try:
            raw = store.get_bytes(quality_key(month, root))
        except NotFoundError:
            raise DataValidationError(f"quality report for {month} is missing") from None
        reports.append(QualityReport.model_validate_json(raw))
    return reports


def _read_labelled_silver(
    store: ObjectStore, days: Sequence[date], workdir: Path, root: str
) -> tuple[pd.DataFrame, int]:
    """Labelled rows of ``days`` (sorted by event_id) and the number of cancelled rows."""
    files = []
    for day in days:
        path = workdir / f"silver-{day:%Y-%m-%d}.parquet"
        try:
            store.download_file(silver_key(day, root), path)
        except NotFoundError:
            raise DataValidationError(f"silver day {day} is missing") from None
        files.append(str(path))
    con = duckdb.connect()
    try:
        con.execute("SET TimeZone = 'UTC'")
        params = {"files": files}
        counted = con.execute(
            "SELECT count(*) FROM read_parquet($files) WHERE is_late IS NULL", params
        ).fetchone()
        rows = con.execute(
            "SELECT * FROM read_parquet($files) WHERE is_late IS NOT NULL ORDER BY event_id",
            params,
        ).df()
    finally:
        con.close()
    return rows, int(counted[0]) if counted else 0


def build_snapshot(
    store: ObjectStore, cfg: TrainingConfig, workdir: Path, *, root: str = ""
) -> SnapshotManifest:
    """Build (or reuse) the gold snapshot for the configured window.

    Raises:
        DataValidationError: missing silver day or quality report, too-short window,
            or an empty split.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    end = cfg.end_date or latest_silver_day(store, root)
    windows = compute_windows(end, cfg)
    rows, cancelled = _read_labelled_silver(store, windows.days(), workdir, root)
    excluded = {"cancelled": cancelled, "gap_hours": 0, "gap_station_days": 0}
    quality_months: list[str] = []
    if cfg.exclude_data_gaps:
        quality_months = windows.months()
        in_hour, in_day = gap_masks(rows, _load_reports(store, quality_months, root))
        excluded["gap_hours"] = int(in_hour.sum())
        excluded["gap_station_days"] = int((in_day & ~in_hour).sum())
        rows = rows[~(in_hour | in_day)].reset_index(drop=True)
    split = assign_split(rows["planned_departure_utc"], windows).to_numpy()

    digest = hashlib.sha256()
    infos: dict[str, SplitInfo] = {}
    paths: dict[str, Path] = {}
    for name, (lo, hi) in windows.split_ranges().items():
        part = rows[split == name].reset_index(drop=True)[list(SILVER_COLUMNS)]
        if part.empty:
            raise DataValidationError(f"split {name} is empty")
        data = to_parquet_bytes(part, SILVER_ARROW_SCHEMA)
        digest.update(data)
        paths[name] = workdir / f"{name}.parquet"
        paths[name].write_bytes(data)
        infos[name] = SplitInfo(
            start=lo, end=hi, rows=len(part), late_rate=float(part["is_late"].mean())
        )
    content_hash = digest.hexdigest()
    manifest = SnapshotManifest(
        snapshot_id=f"{end:%Y-%m-%d}_{content_hash[:8]}",
        content_hash=content_hash,
        window_start=windows.start,
        window_end=windows.end,
        splits=infos,
        excluded=excluded,
        silver_days=len(windows.days()),
        quality_months=quality_months,
        config={
            key: value
            for key, value in cfg.model_dump(mode="json").items()
            if key in _SPLIT_CONFIG_KEYS
        }
        | {"end_date": end.isoformat()},
    )
    prefix = snapshot_prefix(manifest.snapshot_id, root)
    if store.exists(prefix + SNAPSHOT_FILE):
        return SnapshotManifest.model_validate_json(store.get_bytes(prefix + SNAPSHOT_FILE))
    for name, path in paths.items():
        store.upload_file(prefix + f"{name}.parquet", path)
    store.put_bytes(
        prefix + SNAPSHOT_FILE, manifest.model_dump_json(indent=2).encode(), "application/json"
    )
    return manifest


def load_snapshot_split(
    store: ObjectStore, snapshot_id: str, split: str, workdir: Path, *, root: str = ""
) -> pd.DataFrame:
    """Download one split of a snapshot and read it."""
    path = workdir / f"load-{split}.parquet"
    store.download_file(snapshot_prefix(snapshot_id, root) + f"{split}.parquet", path)
    return pd.read_parquet(path)
