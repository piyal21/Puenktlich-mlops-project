"""Silver days from HF raw scrapes until HF publishes the month (Phase 9, spec §4.5).

HF's monthly parquet for the running month appears only after the month ends. The training
window must not have holes, so the days between the last HF month and the first live day are
rebuilt from HF's raw daily scrape with the Phase 7 parity code (100 % parity with HF silver)
and stored like HF days (`source=hf`) with one partial-month quality report.
"""

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from dbdelay.config import get_settings
from dbdelay.data.hf_backfill import hf_download
from dbdelay.data.quality import QualityReport, content_hash, gap_fields_for_days
from dbdelay.data.silver import SILVER_ARROW_SCHEMA, quality_key, silver_key, to_parquet_bytes
from dbdelay.data.stations import Station, load_stations
from dbdelay.errors import DataValidationError, DbDelayError
from dbdelay.live.hf_raw import hf_raw_paths
from dbdelay.live.parity import build_hf_raw_silver
from dbdelay.storage import ObjectStore, get_optional_bytes, make_s3_client

FILL_MARKER = "hf_raw_fill_days"
RAW_DIR = Path("data/raw/hf_raw")

DayBuilder = Callable[[date], tuple[pd.DataFrame, dict[str, int]]]


def raw_day_builder(stations: Sequence[Station], raw_dir: Path = RAW_DIR) -> DayBuilder:
    """Download one day's HF raw files and build its silver (network; laptop only)."""

    def build(day: date) -> tuple[pd.DataFrame, dict[str, int]]:
        local = [hf_download(path, raw_dir)[0] for path in hf_raw_paths(day)]
        return build_hf_raw_silver(local, stations, day)

    return build


def fill_hf_raw_days(
    store: ObjectStore,
    station_evas: Sequence[str],
    days: Sequence[date],
    build: DayBuilder,
) -> QualityReport:
    """Write silver for ``days`` (one month) and a quality report covering exactly them.

    Pass the whole range on every run: the report is replaced, not merged.

    Raises:
        DataValidationError: days span several months, or the month already has a real HF
            monthly report (only a report written by this fill may be replaced).
    """
    months = {f"{d:%Y-%m}" for d in days}
    if len(months) != 1:
        raise DataValidationError(f"fill one month at a time, got {sorted(months)}")
    (month,) = months
    existing = get_optional_bytes(store, quality_key(month))
    if (
        existing is not None
        and FILL_MARKER not in QualityReport.model_validate_json(existing).drops
    ):
        raise DataValidationError(f"{month} already has an HF monthly report; not overwriting")
    frames = []
    totals: Counter[str] = Counter()
    for day in sorted(days):
        silver, stats = build(day)
        silver = silver.assign(source="hf")
        store.put_bytes(silver_key(day), to_parquet_bytes(silver, SILVER_ARROW_SCHEMA))
        frames.append(silver)
        totals.update(stats)
    silver = pd.concat(frames, ignore_index=True)
    low_hours, stations = gap_fields_for_days(silver, sorted(days), station_evas)
    labelled = silver["is_late"].dropna()
    report = QualityReport(
        month=month,
        rows_read=int(totals.get("responses", 0)),
        rows_out=len(silver),
        drops={**{k: int(v) for k, v in totals.items()}, FILL_MARKER: len(days)},
        quarantined={},
        null_rates={},
        late_rate=float(labelled.astype(bool).mean()) if len(labelled) else None,
        cancelled_rate=float(silver["is_cancelled"].mean()) if len(silver) else None,
        stations=stations,
        low_volume_hours=low_hours,
        edge_complete={"prev": True, "next": True},
        content_hash=content_hash(silver),
    )
    store.put_bytes(
        quality_key(month), report.model_dump_json(indent=2).encode(), "application/json"
    )
    return report


def hf_raw_fill_main(argv: Sequence[str] | None = None) -> int:
    """CLI for `make hf-raw-fill FROM=YYYY-MM-DD TO=YYYY-MM-DD` (local MinIO)."""
    parser = argparse.ArgumentParser(description="Silver days from HF raw (one month).")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    days = [args.start + timedelta(days=i) for i in range((args.end - args.start).days + 1)]
    settings = get_settings()
    stations = load_stations(settings.stations_file)
    store = ObjectStore(make_s3_client(settings), settings.data_bucket)
    try:
        report = fill_hf_raw_days(store, [s.eva for s in stations], days, raw_day_builder(stations))
    except DbDelayError as exc:
        sys.stderr.write(f"hf-raw fill failed: {exc}\n")
        return 1
    sys.stdout.write(
        f"{report.month}: {len(days)} days, {report.rows_out} rows, "
        f"late {report.late_rate}, low hours {len(report.low_volume_hours)}, "
        f"flagged stations {sum(1 for s in report.stations if s.volume_drop_days)}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(hf_raw_fill_main())
