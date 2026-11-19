"""Daily monitor: score all labelled live departures of one day with the champion (spec §4.1).

Writes `monitoring/daily/date=D.json`, upserts `monitoring/history.json` and, after N bad days
in a row, `control/retrain_requested.json`. Never imports scikit-learn (runs on the API image).
"""

import io
import json
from datetime import date, datetime

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, ValidationError

from dbdelay.data.gaps import gap_masks
from dbdelay.data.live_report import LiveQualityReport, live_quality_key, live_silver_key
from dbdelay.errors import ArtifactIntegrityError, DataValidationError, ModelNotAvailableError
from dbdelay.features.build import build_features
from dbdelay.features.spec import FEATURE_COLUMNS
from dbdelay.logging import get_logger
from dbdelay.monitoring.config import MonitoringConfig
from dbdelay.monitoring.drift import DriftResult, compute_drift
from dbdelay.monitoring.history import (
    DailyResult,
    DayStatus,
    HistoryEntry,
    upsert,
    verdict_for,
)
from dbdelay.monitoring.performance import PerformanceMetrics, performance
from dbdelay.monitoring.triggers import RetrainRequest, ThresholdOverrides, evaluate_trigger
from dbdelay.registry.artifacts import ModelBundle, load_bundle, load_reference_sample
from dbdelay.registry.pointer import ModelPointer
from dbdelay.storage import ObjectStore, get_optional_bytes
from dbdelay.training.train import predict_raw

HISTORY_KEY = "monitoring/history.json"
RETRAINS_KEY = "monitoring/retrains.json"
FLAG_KEY = "control/retrain_requested.json"
JSON = "application/json"


def daily_key(day: date) -> str:
    return f"monitoring/daily/date={day:%Y-%m-%d}.json"


class MonitorOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    result: DailyResult
    flag_written: RetrainRequest | None


def read_history(store: ObjectStore) -> list[HistoryEntry]:
    """Stored history (empty before the first run).

    Raises:
        DataValidationError: if the stored file is not a valid history.
    """
    raw = get_optional_bytes(store, HISTORY_KEY)
    if raw is None:
        return []
    try:
        return [HistoryEntry.model_validate(e) for e in json.loads(raw)]
    except (ValueError, ValidationError):
        raise DataValidationError(f"{HISTORY_KEY} is not a valid history") from None


def read_flag(store: ObjectStore) -> RetrainRequest | None:
    """The current retrain flag, or None (an unreadable flag counts as none, and is logged)."""
    raw = get_optional_bytes(store, FLAG_KEY)
    if raw is None:
        return None
    try:
        return RetrainRequest.model_validate_json(raw)
    except ValidationError:
        get_logger("monitor").warning("retrain flag unreadable, treated as absent")
        return None


def _already_requested(current: RetrainRequest | None, request: RetrainRequest) -> bool:
    """A waiting request, or one (any state) for the same model whose days overlap this streak.

    Without this, a streak that stays bad after a rejected retrain would request a retrain
    every day; a new request needs a fresh, non-overlapping streak (final review, Phase 9).
    """
    if current is None:
        return False
    if current.state == "requested":
        return True
    return current.model_version == request.model_version and max(current.days) >= min(request.days)


def _champion(models: ObjectStore, pointer: ModelPointer) -> tuple[ModelBundle, float]:
    state = pointer.get()
    if state is None:
        raise ModelNotAvailableError("no champion to monitor")
    bundle = load_bundle(models, state.champion_version)
    test_brier = bundle.manifest.metrics.get("test_brier")
    if test_brier is None or test_brier <= 0:
        raise ArtifactIntegrityError(f"models/{state.champion_version}: no test_brier in manifest")
    return bundle, test_brier


def _labelled(data: ObjectStore, day: date, excluded: dict[str, int]) -> pd.DataFrame | None:
    """Labelled rows outside flagged gaps, or None when the day has no silver."""
    raw = get_optional_bytes(data, live_silver_key(day))
    if raw is None:
        return None
    silver = pd.read_parquet(io.BytesIO(raw))
    labelled = silver[silver["is_late"].notna()]
    excluded["cancelled"] = len(silver) - len(labelled)
    report_raw = get_optional_bytes(data, live_quality_key(day))
    if report_raw is None:
        get_logger("monitor").warning(
            "quality report missing, no gap filtering", extra={"day": str(day)}
        )
        excluded["gap_hour"] = excluded["gap_station_day"] = 0
        return labelled
    in_hour, in_day = gap_masks(labelled, [LiveQualityReport.model_validate_json(report_raw)])
    excluded["gap_hour"] = int(in_hour.sum())
    excluded["gap_station_day"] = int((in_day & ~in_hour).sum())
    return labelled[~(in_hour | in_day).to_numpy()]


def _score(
    models: ObjectStore,
    bundle: ModelBundle,
    rows: pd.DataFrame,
    cfg: MonitoringConfig,
) -> tuple[DayStatus, PerformanceMetrics, DriftResult | None]:
    if rows.empty:  # e.g. every station-day flagged as a gap (2026-11-13, the first live day)
        return "too_few_rows", PerformanceMetrics(rows=0), None
    features = build_features(rows, bundle.spec)
    p = bundle.calibrator.apply(predict_raw(bundle.booster, features))
    y = rows["is_late"].to_numpy(dtype=bool)
    perf = performance(y, p, cfg.ece_bins)
    if len(rows) < cfg.min_labeled_rows:
        return "too_few_rows", perf, None
    reference = load_reference_sample(models, bundle.manifest.version)
    drift = compute_drift(
        features,
        reference[list(FEATURE_COLUMNS)],
        p,
        reference["p_late"].to_numpy(dtype=float),
        cfg,
    )
    return "ok", perf, drift


def run_monitor(  # noqa: PLR0913 - keyword-only, one call site per runtime
    *,
    data: ObjectStore,
    models: ObjectStore,
    pointer: ModelPointer,
    day: date,
    cfg: MonitoringConfig,
    now: datetime,
    overrides: ThresholdOverrides | None = None,
) -> MonitorOutcome:
    """Monitor one UTC day and update history and the retrain flag.

    Raises:
        ModelNotAvailableError: no champion.
        ArtifactIntegrityError: champion bundle or reference sample fails verification.
        DataValidationError: stored history is corrupt.
    """
    bundle, test_brier = _champion(models, pointer)
    excluded: dict[str, int] = {}
    rows = _labelled(data, day, excluded)
    drift: DriftResult | None = None
    if rows is None:
        status: DayStatus = "no_data"
        perf = PerformanceMetrics(rows=0)
    else:
        status, perf, drift = _score(models, bundle, rows, cfg)
    brier_ratio = None if perf.brier is None else perf.brier / test_brier
    result = DailyResult(
        day=day,
        status=status,
        model_version=bundle.manifest.version,
        champion_test_brier=test_brier,
        performance=perf,
        brier_ratio=brier_ratio,
        drift=drift,
        verdict=verdict_for(perf, brier_ratio, drift, status, cfg),
        excluded_rows=excluded,
        computed_at=now,
    )
    data.put_bytes(daily_key(day), result.model_dump_json(indent=2).encode(), JSON)
    history = upsert(read_history(data), HistoryEntry.from_daily(result), cfg.history_max_days)
    data.put_bytes(
        HISTORY_KEY,
        json.dumps([e.model_dump(mode="json") for e in history]).encode(),
        JSON,
    )
    request = evaluate_trigger(history, cfg, now=now, overrides=overrides)
    written = None
    if request is not None and not _already_requested(read_flag(data), request):
        data.put_bytes(FLAG_KEY, request.model_dump_json(indent=2).encode(), JSON)
        written = request
    get_logger("monitor").info(
        "day monitored",
        extra={
            "day": str(day),
            "status": status,
            "rows": perf.rows,
            "brier_ratio": brier_ratio,
            "drift_share": None if drift is None else drift.drift_share,
            "flag": None if written is None else written.request_id,
            "mean_p_late": None
            if perf.mean_p_late is None
            else float(np.round(perf.mean_p_late, 4)),
        },
    )
    return MonitorOutcome(result=result, flag_written=written)
