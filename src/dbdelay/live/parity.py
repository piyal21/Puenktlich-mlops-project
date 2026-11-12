"""Parity check (Phase 7): HF raw XML through *our* live code vs. HF silver (their processing).

Same inputs on both sides, so every difference is a rule difference between our live conform and
the HF data the model was trained on. Laptop-only; needs `make up` (MinIO with HF silver).
"""

import argparse
import io
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pandas as pd

from dbdelay.config import get_settings
from dbdelay.data.hf_backfill import hf_download
from dbdelay.data.silver import silver_key
from dbdelay.data.stations import Station, alias_map, load_stations
from dbdelay.errors import DataValidationError, DbDelayError
from dbdelay.live.conform import StopMerger, to_silver
from dbdelay.live.hf_raw import hf_raw_paths, iter_hf_raw
from dbdelay.live.reconcile import compare_silver
from dbdelay.storage import ObjectStore, make_s3_client
from dbdelay.timetables.parser import parse_timetable

RAW_DIR = Path("data/raw/hf_raw")
OUT_DIR = Path("data/reconciliation")


def build_hf_raw_silver(
    paths: Sequence[Path], stations: Sequence[Station], day: date
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Silver for UTC ``day`` from HF raw files, observations merged in time order."""
    merger = StopMerger()
    stats = {"responses": 0, "parse_errors": 0}
    eva_map = alias_map(stations)
    for path in paths:
        for kind, eva, seen_at, xml in iter_hf_raw(path, eva_map):
            stats["responses"] += 1
            try:
                events = parse_timetable(xml, eva).events
            except DataValidationError:
                stats["parse_errors"] += 1
                continue
            for event in events:
                if kind == "plan":
                    merger.add_plan(event, seen_at)
                else:
                    merger.add_change(event, seen_at)
    silver, drops = to_silver(merger.stops(), stations, day)
    stats |= drops | {"fchg_only_stops": len(merger.fchg_only())}
    return silver, stats


def mismatches(ours: pd.DataFrame, theirs: pd.DataFrame) -> pd.DataFrame:
    """Rows only on one side, or matched rows that differ (for reading, not for metrics)."""
    cols = [
        "event_id", "eva", "ride_id", "stop_index", "train_type", "final_destination",
        "planned_departure_utc", "changed_departure_utc", "delay_min", "is_cancelled",
    ]  # fmt: skip
    joined = ours[cols].merge(
        theirs[cols], on="event_id", how="outer", suffixes=("_live", "_hf"), indicator=True
    )
    same = (
        (joined["delay_min_live"] == joined["delay_min_hf"]).fillna(False).astype(bool)
        & (joined["is_cancelled_live"] == joined["is_cancelled_hf"]).fillna(False).astype(bool)
        & (joined["final_destination_live"].fillna("") == joined["final_destination_hf"].fillna(""))
    )
    return joined[(joined["_merge"] != "both") | ~same]


def parity_main(argv: Sequence[str] | None = None) -> int:
    """CLI for `make parity-check DAY=YYYY-MM-DD`."""
    parser = argparse.ArgumentParser(description="HF raw through live code vs HF silver.")
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    settings = get_settings()
    stations = load_stations(settings.stations_file)
    try:
        local = [hf_download(p, RAW_DIR)[0] for p in hf_raw_paths(args.day)]
        ours, stats = build_hf_raw_silver(local, stations, args.day)
        store = ObjectStore(make_s3_client(settings), settings.data_bucket)
        theirs = pd.read_parquet(io.BytesIO(store.get_bytes(silver_key(args.day))))
    except DbDelayError as exc:
        sys.stderr.write(f"parity check failed: {exc}\n")
        return 1
    comparison = compare_silver(ours, theirs, args.day)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"parity-{args.day}.json").write_text(
        comparison.to_json(), encoding="utf-8", newline="\n"
    )
    mismatches(ours, theirs).to_csv(OUT_DIR / f"parity-{args.day}-mismatches.csv", index=False)
    sys.stdout.write(f"stats: {stats}\n")
    sys.stdout.write(
        f"live {comparison.left_rows} / hf {comparison.right_rows} / matched {comparison.matched}"
        f" | match live {comparison.match_rate_left} hf {comparison.match_rate_right}"
        f" | is_late {comparison.is_late_agreement} delay {comparison.delay_exact}"
        f" | cancelled {comparison.cancelled_agreement}"
        f" | destination {comparison.destination_agreement}\n"
    )
    return 0
