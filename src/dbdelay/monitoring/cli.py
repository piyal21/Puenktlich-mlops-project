"""`make monitor-local DAY=YYYY-MM-DD`: run the daily monitor against the configured store."""

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime

from dbdelay.config import get_settings
from dbdelay.errors import DbDelayError
from dbdelay.monitoring.config import load_monitoring_config
from dbdelay.monitoring.job import run_monitor
from dbdelay.monitoring.triggers import ThresholdOverrides
from dbdelay.registry.pointer import make_pointer
from dbdelay.storage import ObjectStore, make_s3_client


def monitor_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monitor one UTC day of live silver.")
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--drift-share-max", type=float, default=None)
    parser.add_argument("--brier-ratio-max", type=float, default=None)
    args = parser.parse_args(argv)
    overrides = None
    if args.drift_share_max is not None or args.brier_ratio_max is not None:
        overrides = ThresholdOverrides(
            drift_share_max=args.drift_share_max, brier_ratio_max=args.brier_ratio_max
        )
    settings = get_settings()
    client = make_s3_client(settings)
    models = ObjectStore(client, settings.models_bucket)
    try:
        outcome = run_monitor(
            data=ObjectStore(client, settings.data_bucket),
            models=models,
            pointer=make_pointer(settings, models),
            day=args.day,
            cfg=load_monitoring_config(settings.monitoring_config_file),
            now=datetime.now(UTC),
            overrides=overrides,
        )
    except DbDelayError as exc:
        sys.stderr.write(f"monitor failed: {exc}\n")
        return 1
    r = outcome.result
    sys.stdout.write(
        f"{r.day} {r.status} rows={r.performance.rows} brier={r.performance.brier}"
        f" ratio={r.brier_ratio} drift_share={None if r.drift is None else r.drift.drift_share}"
        f" flag={None if outcome.flag_written is None else outcome.flag_written.request_id}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(monitor_main())
