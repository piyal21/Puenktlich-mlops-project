"""Laptop side of the retrain loop (route A, spec §4.5): AWS ⇄ local MinIO.

`make retrain-sync`: live silver + live quality reports missing in MinIO, and a `requested`
retrain flag (AWS copy marked `picked_up` so the monitor may raise the next one).
`make retrain-publish`: new local decision records → `monitoring/retrains.json` on S3; a
gate-approved release also publishes the local champion to prod (bundle + SSM pointer).
"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from botocore.exceptions import BotoCoreError
from pydantic import BaseModel, ConfigDict

from dbdelay.aws_session import refusal_line
from dbdelay.config import get_settings
from dbdelay.data.live_report import live_quality_key, live_silver_key
from dbdelay.errors import DataValidationError, DbDelayError
from dbdelay.monitoring.control import mark, read_decisions
from dbdelay.monitoring.history import RetrainRecord
from dbdelay.monitoring.job import FLAG_KEY, RETRAINS_KEY, read_flag
from dbdelay.registry.pointer import ModelPointer, ObjectStorePointer, SsmPointer, make_ssm_client
from dbdelay.registry.publish import DEFAULT_POINTER_PARAM, DEFAULT_REGION, publish_champion
from dbdelay.storage import ObjectStore, get_optional_bytes, make_aws_s3_client, make_s3_client

JSON = "application/json"
FIRST_LIVE_DAY = date(2026, 11, 13)  # first nightly live silver day (Phase 7)


class SyncReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    days_copied: list[date]
    days_missing: list[date]
    flag: str | None


def retrain_sync(
    aws: ObjectStore, local: ObjectStore, *, first_day: date, today: date, now: datetime
) -> SyncReport:
    """Copy live days ``first_day``..``today``-1 missing locally, and a waiting retrain flag."""
    copied: list[date] = []
    missing: list[date] = []
    day = first_day
    while day < today:
        if not local.exists(live_silver_key(day)):
            silver = get_optional_bytes(aws, live_silver_key(day))
            report = get_optional_bytes(aws, live_quality_key(day))
            if silver is None or report is None:
                missing.append(day)
            else:
                local.put_bytes(live_quality_key(day), report, JSON)
                local.put_bytes(live_silver_key(day), silver)
                copied.append(day)
        day += timedelta(days=1)

    flag_id = None
    remote = read_flag(aws)
    running = read_flag(local)
    busy = running is not None and running.state != "done"
    if remote is not None and remote.state == "requested" and not busy:
        local.put_bytes(FLAG_KEY, remote.model_dump_json(indent=2).encode(), JSON)
        mark(aws, remote, "picked_up", now)
        flag_id = remote.request_id
    return SyncReport(days_copied=copied, days_missing=missing, flag=flag_id)


def _published(aws: ObjectStore) -> list[RetrainRecord]:
    raw = get_optional_bytes(aws, RETRAINS_KEY)
    if raw is None:
        return []
    try:
        return [RetrainRecord.model_validate(r) for r in json.loads(raw)]
    except ValueError:
        raise DataValidationError(f"{RETRAINS_KEY} is not a valid list") from None


def retrain_publish(  # noqa: PLR0913 - keyword-only: two stores per side + two pointers
    *,
    local_data: ObjectStore,
    local_models: ObjectStore,
    aws_data: ObjectStore,
    aws_models: ObjectStore,
    local_pointer: ModelPointer,
    aws_pointer: ModelPointer,
) -> list[RetrainRecord]:
    """Publish new decisions; release the local champion when a new decision released it.

    Returns the newly published records (re-runs publish nothing twice).
    """
    published = _published(aws_data)
    seen = {r.request_id for r in published}
    new = [r for r in read_decisions(local_data) if r.request_id not in seen]
    if not new:
        return []
    local = local_pointer.get()
    released = [
        r
        for r in new
        if r.decision == "released" and local and r.champion_after == local.champion_version
    ]
    if released:
        publish_champion(local_models, local_pointer, aws_models, aws_pointer)
    records = [r.model_dump(mode="json") for r in [*published, *new]]
    aws_data.put_bytes(RETRAINS_KEY, json.dumps(records, indent=2).encode(), JSON)
    return new


def retrain_main(argv: Sequence[str] | None = None) -> int:
    """CLI for `make retrain-sync` / `make retrain-publish` (AWS via the laptop's login)."""
    parser = argparse.ArgumentParser(description="Retrain loop: AWS <-> local MinIO.")
    parser.add_argument("action", choices=["sync", "publish"])
    parser.add_argument("--data-bucket", required=True)
    parser.add_argument("--models-bucket", required=True)
    parser.add_argument("--pointer-param", default=DEFAULT_POINTER_PARAM)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--from", dest="first_day", type=date.fromisoformat, default=FIRST_LIVE_DAY)
    args = parser.parse_args(argv)
    settings = get_settings()
    local_client = make_s3_client(settings)
    local_data = ObjectStore(local_client, settings.data_bucket)
    local_models = ObjectStore(local_client, settings.models_bucket)
    now = datetime.now(UTC)
    try:
        aws_client = make_aws_s3_client(args.region)
        aws_data = ObjectStore(aws_client, args.data_bucket)
        if args.action == "sync":
            report = retrain_sync(
                aws_data, local_data, first_day=args.first_day, today=now.date(), now=now
            )
            sys.stdout.write(
                f"copied {len(report.days_copied)} days,"
                f" missing {[str(d) for d in report.days_missing]},"
                f" flag {report.flag}\n"
            )
        else:
            new = retrain_publish(
                local_data=local_data,
                local_models=local_models,
                aws_data=aws_data,
                aws_models=ObjectStore(aws_client, args.models_bucket),
                local_pointer=ObjectStorePointer(local_models),
                aws_pointer=SsmPointer(make_ssm_client(args.region), args.pointer_param),
            )
            sys.stdout.write(
                "published "
                + (", ".join(f"{r.request_id}={r.decision}" for r in new) or "nothing")
                + "\n"
            )
    except (DbDelayError, BotoCoreError) as exc:
        sys.stderr.write(refusal_line(f"retrain-{args.action}", exc) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(retrain_main())
