"""Retrain trigger: N consecutive bad days for the same reason with the same model (spec §4.4)."""

from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict

from dbdelay.monitoring.config import MonitoringConfig
from dbdelay.monitoring.history import HistoryEntry

Reason = Literal["drift", "performance"]


class ThresholdOverrides(BaseModel):
    """Manual-invocation overrides for the forced demo; they never change stored verdicts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    drift_share_max: float | None = None
    brier_ratio_max: float | None = None


class RetrainRequest(BaseModel):
    """`control/retrain_requested.json`."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    state: Literal["requested", "picked_up", "done"]
    reason: Reason
    days: list[date]
    model_version: str
    metrics: dict[str, float | None]
    forced: bool
    thresholds: dict[str, float]
    requested_at: datetime
    picked_up_at: datetime | None = None


def _is_streak(entries: list[HistoryEntry]) -> bool:
    return all(e.status == "ok" for e in entries) and all(
        b.day == a.day + timedelta(days=1) and b.model_version == a.model_version
        for a, b in pairwise(entries)
    )


def evaluate_trigger(
    history: list[HistoryEntry],
    cfg: MonitoringConfig,
    *,
    now: datetime,
    overrides: ThresholdOverrides | None = None,
) -> RetrainRequest | None:
    """A retrain request when the newest ``consecutive_days`` entries are all bad, else None."""
    entries = sorted(history, key=lambda e: e.day)[-cfg.consecutive_days :]
    if len(entries) < cfg.consecutive_days or not _is_streak(entries):
        return None
    drift_max = cfg.drift_share_max
    ratio_max = cfg.brier_ratio_max
    if overrides is not None:
        drift_max = (
            overrides.drift_share_max if overrides.drift_share_max is not None else drift_max
        )
        ratio_max = (
            overrides.brier_ratio_max if overrides.brier_ratio_max is not None else ratio_max
        )

    def drift_bad(e: HistoryEntry) -> bool:
        if overrides is None:
            return e.verdict.drift_bad
        return e.drift_share is not None and e.drift_share > drift_max

    def performance_bad(e: HistoryEntry) -> bool:
        if overrides is None:
            return e.verdict.performance_bad
        return e.brier_ratio is not None and e.brier_ratio > ratio_max

    reason: Reason
    if all(drift_bad(e) for e in entries):
        reason = "drift"
        metrics = {str(e.day): e.drift_share for e in entries}
    elif all(performance_bad(e) for e in entries):
        reason = "performance"
        metrics = {str(e.day): e.brier_ratio for e in entries}
    else:
        return None
    return RetrainRequest(
        request_id=f"{now:%Y%m%dT%H%M%SZ}-{reason}",
        state="requested",
        reason=reason,
        days=[e.day for e in entries],
        model_version=entries[-1].model_version,
        metrics=metrics,
        forced=overrides is not None,
        thresholds={"drift_share_max": drift_max, "brier_ratio_max": ratio_max},
        requested_at=now,
    )
