"""Laptop CLIs for the live jobs against local MinIO (`make ingest-local`, `make etl-live-local`).

Keys come from `.env` (never printed). Same code paths as the Lambda handlers.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, date, datetime

from dbdelay.config import get_settings
from dbdelay.errors import DbDelayError
from dbdelay.live.jobs import etl_job, ingest_job, make_client, yesterday


def ingest_main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(description="One live ingest run into local storage.").parse_args(argv)
    settings = get_settings()
    started = datetime.now(UTC)
    try:
        result = ingest_job(settings, make_client(settings), now=started)
    except DbDelayError as exc:
        sys.stderr.write(f"ingest failed: {exc}\n")
        return 1
    seconds = (datetime.now(UTC) - started).total_seconds()
    sys.stdout.write(f"ingest done in {seconds:.1f} s: {asdict(result)}\n")
    return 0


def etl_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live bronze -> silver for one UTC day.")
    parser.add_argument("--day", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    day = args.day or yesterday(datetime.now(UTC))
    started = datetime.now(UTC)
    try:
        report = etl_job(get_settings(), day)
    except DbDelayError as exc:
        sys.stderr.write(f"etl failed: {exc}\n")
        return 1
    seconds = (datetime.now(UTC) - started).total_seconds()
    flagged = [s.eva for s in report.stations if s.volume_drop_days]
    sys.stdout.write(
        f"etl {day} done in {seconds:.1f} s: rows {report.rows_out}, flagged stations "
        f"{len(flagged)}, low hours {report.low_volume_hours}, empty {report.empty_stations}\n"
    )
    return 0
