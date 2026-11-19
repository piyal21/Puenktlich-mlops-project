"""AWS Lambda entry point `monitor` (04:30 Berlin, API image): monitor one UTC day.

Event `{}` = yesterday (UTC). Manual runs may pass `{"day": "YYYY-MM-DD"}` (backfill, idempotent)
and `{"threshold_overrides": {...}}` (forced demo; never changes stored verdicts). Emits the
EMF metrics `LabeledEvents` (always) and `BrierDaily`, `AUCDaily`, `BrierRatio`, `DriftShare`
(only for `ok` days, so alarms never see numbers from days that do not count).
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from aws_lambda_powertools import Metrics
from aws_lambda_powertools.metrics import MetricUnit
from pydantic import BaseModel, ConfigDict

from dbdelay.config import get_settings
from dbdelay.logging import get_logger
from dbdelay.monitoring.config import load_monitoring_config
from dbdelay.monitoring.job import MonitorOutcome, run_monitor
from dbdelay.monitoring.triggers import ThresholdOverrides
from dbdelay.registry.pointer import make_pointer
from dbdelay.storage import ObjectStore, make_s3_client


class MonitorEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: date | None = None
    threshold_overrides: ThresholdOverrides | None = None


def _emit(outcome: MonitorOutcome) -> None:
    r = outcome.result
    metrics = Metrics(namespace="Puenktlich", service="monitor")
    metrics.add_metric(name="LabeledEvents", unit=MetricUnit.Count, value=r.performance.rows)
    if r.status == "ok":
        values = {
            "BrierDaily": r.performance.brier,
            "AUCDaily": r.performance.roc_auc,
            "BrierRatio": r.brier_ratio,
            "DriftShare": None if r.drift is None else r.drift.drift_share,
        }
        for name, value in values.items():
            if value is not None:
                metrics.add_metric(name=name, unit=MetricUnit.NoUnit, value=value)
    metrics.flush_metrics()


def _payload(event: dict[str, Any] | None) -> dict[str, Any]:
    """Manual payload, or {} for EventBridge Scheduler's envelope (a target without input)."""
    if not event or event.get("source") == "aws.scheduler":
        return {}
    return event


def handler(event: dict[str, Any] | None, context: object) -> dict[str, Any]:
    request = MonitorEvent.model_validate(_payload(event))
    now = datetime.now(UTC)
    day = request.day or (now - timedelta(days=1)).date()
    settings = get_settings()
    client = make_s3_client(settings)
    models = ObjectStore(client, settings.models_bucket)
    if request.threshold_overrides is not None:
        get_logger("monitor").warning(
            "threshold overrides (forced run)",
            extra={"overrides": request.threshold_overrides.model_dump()},
        )
    outcome = run_monitor(
        data=ObjectStore(client, settings.data_bucket),
        models=models,
        pointer=make_pointer(settings, models),
        day=day,
        cfg=load_monitoring_config(settings.monitoring_config_file),
        now=now,
        overrides=request.threshold_overrides,
    )
    _emit(outcome)
    flag = outcome.flag_written
    return {
        "day": day.isoformat(),
        "status": outcome.result.status,
        "rows": outcome.result.performance.rows,
        "flag": None if flag is None else flag.request_id,
    }
