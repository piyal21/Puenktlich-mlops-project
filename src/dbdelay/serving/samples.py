"""Sample silver days for the cloud sample board (Phase 6, until live ingestion in Phase 7).

The `sample_board` Lambda replays one of these real days onto "now" (see ``seed.py``).
Nine consecutive days give every weekday plus its next day, and the UTC day before a Monday
covers that Monday's early Berlin hours.
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import date, timedelta

from dbdelay.aws_session import LOGIN_HINT, is_login_error
from dbdelay.config import get_settings
from dbdelay.data.silver import silver_key
from dbdelay.errors import DbDelayError
from dbdelay.storage import ObjectStore, make_aws_s3_client, make_s3_client

SAMPLE_DAYS: tuple[date, ...] = tuple(date(2026, 8, 23) + timedelta(days=i) for i in range(9))
DEFAULT_REGION = "eu-north-1"


def copy_silver_days(
    source: ObjectStore, target: ObjectStore, days: Sequence[date], target_root: str
) -> int:
    """Copy each day's silver partition byte-identically under ``target_root``.

    Every source day is read before anything is written.

    Raises:
        NotFoundError: if a day is missing in ``source`` (nothing written).
    """
    payloads = [(day, source.get_bytes(silver_key(day))) for day in days]
    for day, data in payloads:
        target.put_bytes(silver_key(day, target_root), data)
    return len(payloads)


def upload_main(argv: Sequence[str] | None = None) -> int:
    """CLI for `make upload-sample-days`: local MinIO silver → AWS data bucket ``samples/``."""
    parser = argparse.ArgumentParser(description="Upload the sample silver days to AWS.")
    parser.add_argument("--data-bucket", required=True, help="AWS data bucket (TF output)")
    parser.add_argument("--region", default=DEFAULT_REGION)
    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        count = copy_silver_days(
            ObjectStore(make_s3_client(settings), settings.data_bucket),
            ObjectStore(make_aws_s3_client(args.region), args.data_bucket),
            SAMPLE_DAYS,
            settings.sample_silver_root,
        )
    except DbDelayError as exc:
        sys.stderr.write((LOGIN_HINT if is_login_error(exc) else f"upload refused: {exc}") + "\n")
        return 1
    sys.stdout.write(f"uploaded {count} sample days to {settings.sample_silver_root}\n")
    return 0
