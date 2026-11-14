"""AWS Lambda entry point `ingest` (every 15 min): DB API → bronze, plan cache, live board.

Emits the EMF metric `EventsIngested`; a failed run raises (Lambda error alarm, last board stays).
The DB API client (keys from SSM) is built once per cold start.
"""

import math
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from aws_lambda_powertools import Metrics
from aws_lambda_powertools.metrics import MetricUnit

from dbdelay.config import get_settings
from dbdelay.live.ingest import TimetablesApi
from dbdelay.live.jobs import ingest_job, make_client
from dbdelay.logging import get_logger

_client: TimetablesApi | None = None


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    global _client  # noqa: PLW0603 - reuse the client across warm invocations
    settings = get_settings()
    if _client is None:
        _client = make_client(settings)
    remaining = getattr(context, "get_remaining_time_in_millis", None)
    try:
        result = ingest_job(
            settings,
            _client,
            now=datetime.now(UTC),
            time_left=(lambda: remaining() / 1000) if remaining else (lambda: math.inf),
        )
    except Exception:
        _client = None  # e.g. keys rotated in SSM: the next invocation reloads them
        raise
    metrics = Metrics(namespace="Puenktlich", service="ingest")
    metrics.add_metric(name="EventsIngested", unit=MetricUnit.Count, value=result.events)
    metrics.flush_metrics()
    out = asdict(result) | {"failed_stations": list(result.failed_stations)}
    get_logger("ingest").info("ingest run done", extra=out)
    return out
