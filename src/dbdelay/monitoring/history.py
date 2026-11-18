"""Daily monitoring results, the compact history behind the health page, and retrain records."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from dbdelay.monitoring.config import MonitoringConfig
from dbdelay.monitoring.drift import DriftResult
from dbdelay.monitoring.performance import PerformanceMetrics

DayStatus = Literal["ok", "no_data", "too_few_rows"]


class Verdict(BaseModel):
    """A day's bad/good call and the thresholds it was made with (always the config values)."""

    model_config = ConfigDict(frozen=True)

    drift_bad: bool
    performance_bad: bool
    drift_share_max: float
    brier_ratio_max: float


class DailyResult(BaseModel):
    """`monitoring/daily/date=D.json`."""

    model_config = ConfigDict(frozen=True)

    day: date
    status: DayStatus
    model_version: str
    champion_test_brier: float
    performance: PerformanceMetrics
    brier_ratio: float | None
    drift: DriftResult | None
    verdict: Verdict
    excluded_rows: dict[str, int]
    computed_at: datetime


class HistoryEntry(BaseModel):
    """One day in `monitoring/history.json`."""

    model_config = ConfigDict(frozen=True)

    day: date
    status: DayStatus
    model_version: str
    rows: int
    brier: float | None
    roc_auc: float | None
    ece: float | None
    late_rate: float | None
    mean_p_late: float | None
    brier_ratio: float | None
    drift_share: float | None
    prediction_drift: float | None
    verdict: Verdict

    @classmethod
    def from_daily(cls, r: DailyResult) -> "HistoryEntry":
        perf = r.performance
        return cls(
            day=r.day,
            status=r.status,
            model_version=r.model_version,
            rows=perf.rows,
            brier=perf.brier,
            roc_auc=perf.roc_auc,
            ece=perf.ece,
            late_rate=perf.late_rate,
            mean_p_late=perf.mean_p_late,
            brier_ratio=r.brier_ratio,
            drift_share=None if r.drift is None else r.drift.drift_share,
            prediction_drift=None if r.drift is None else r.drift.prediction.score,
            verdict=r.verdict,
        )


class RetrainRecord(BaseModel):
    """One retrain decision, as shown on the health page (`monitoring/retrains.json`)."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    reason: str
    forced: bool
    requested_at: datetime
    decided_at: datetime
    decision: Literal["released", "rejected", "failed"]
    challenger_version: str | None
    champion_before: str | None
    champion_after: str | None
    failed_checks: list[str]


def verdict_for(
    perf: PerformanceMetrics,
    brier_ratio: float | None,
    drift: DriftResult | None,
    status: DayStatus,
    cfg: MonitoringConfig,
) -> Verdict:
    """Bad/good with the config thresholds; only `ok` days can be bad."""
    counts = status == "ok"
    return Verdict(
        drift_bad=counts and drift is not None and drift.drift_share > cfg.drift_share_max,
        performance_bad=counts and brier_ratio is not None and brier_ratio > cfg.brier_ratio_max,
        drift_share_max=cfg.drift_share_max,
        brier_ratio_max=cfg.brier_ratio_max,
    )


def upsert(history: list[HistoryEntry], entry: HistoryEntry, max_days: int) -> list[HistoryEntry]:
    """Replace the entry for the same day (re-runs are idempotent), sort, keep the newest."""
    kept = [e for e in history if e.day != entry.day] + [entry]
    return sorted(kept, key=lambda e: e.day)[-max_days:]
