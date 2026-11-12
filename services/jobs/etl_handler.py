"""AWS Lambda entry point `etl_daily` (03:30 Berlin): live bronze → silver for one UTC day.

Event `{"day": "YYYY-MM-DD"}` re-runs a day; default is yesterday (UTC). Emits `EmptyStations`.
"""

from datetime import UTC, date, datetime
from typing import Any

from aws_lambda_powertools import Metrics
from aws_lambda_powertools.metrics import MetricUnit

from dbdelay.config import get_settings
from dbdelay.live.jobs import etl_job, yesterday
from dbdelay.logging import get_logger


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    raw = (event or {}).get("day")
    day = date.fromisoformat(raw) if raw else yesterday(datetime.now(UTC))
    report = etl_job(get_settings(), day)
    metrics = Metrics(namespace="Puenktlich", service="etl_daily")
    metrics.add_metric(
        name="EmptyStations", unit=MetricUnit.Count, value=len(report.empty_stations)
    )
    metrics.flush_metrics()
    out = {
        "day": day.isoformat(),
        "rows": report.rows_out,
        "empty_stations": report.empty_stations,
        "low_volume_hours": report.low_volume_hours,
        "flagged_stations": [s.eva for s in report.stations if s.volume_drop_days],
    }
    get_logger("etl_daily").info("etl done", extra=out)
    return out
