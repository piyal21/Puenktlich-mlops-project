"""Score departures with the champion: calibrated ``p_late``, risk level, top reasons, and one
prediction log line per scored departure (architecture §7)."""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

import pandas as pd

from dbdelay.errors import ModelNotAvailableError
from dbdelay.features.build import build_features
from dbdelay.features.spec import RiskThresholds
from dbdelay.logging import get_logger
from dbdelay.registry.artifacts import ModelBundle
from dbdelay.serving.board import BoardDeparture
from dbdelay.serving.explain import Factor, top_factors
from dbdelay.training.train import predict_raw

RiskLevel = Literal["low", "medium", "high"]


class ChampionSource(Protocol):
    def get(self) -> ModelBundle: ...


@dataclass(frozen=True)
class ScheduleInput:
    """What the model may know before departure: timetable fields only (leakage policy)."""

    eva: str
    train_type: str
    line_number: str | None
    final_destination: str | None
    stop_index: int
    planned_departure_utc: datetime

    @classmethod
    def from_board(cls, row: BoardDeparture) -> "ScheduleInput":
        return cls(
            eva=row.eva,
            train_type=row.train_type,
            line_number=row.line_number,
            final_destination=row.final_destination,
            stop_index=row.stop_index,
            planned_departure_utc=row.planned_departure_utc,
        )


@dataclass(frozen=True)
class Prediction:
    p_late: float
    risk_level: RiskLevel
    factors: tuple[Factor, ...]


@dataclass(frozen=True)
class BoardScores:
    model_version: str | None
    predictions: list[Prediction | None]


def risk_level(p_late: float, thresholds: RiskThresholds) -> RiskLevel:
    """Low below ``medium``, Medium below ``high``, else High (prd §5)."""
    if p_late < thresholds.medium:
        return "low"
    if p_late < thresholds.high:
        return "medium"
    return "high"


def schedule_frame(inputs: Sequence[ScheduleInput]) -> pd.DataFrame:
    """Rows in the shape ``build_features`` reads (``REQUIRED_COLUMNS``)."""
    return pd.DataFrame(
        {
            "eva": pd.Series([i.eva for i in inputs], dtype="object"),
            "train_type": pd.Series([i.train_type for i in inputs], dtype="object"),
            "line_number": pd.Series([i.line_number for i in inputs], dtype="object"),
            "final_destination": pd.Series([i.final_destination for i in inputs], dtype="object"),
            "stop_index": pd.Series([i.stop_index for i in inputs], dtype="int16"),
            "planned_departure_utc": pd.Series(
                pd.to_datetime([i.planned_departure_utc for i in inputs], utc=True)
            ),
        }
    )


def score(bundle: ModelBundle, inputs: Sequence[ScheduleInput]) -> list[Prediction]:
    """Predictions in input order.

    Raises:
        ModelNotAvailableError: if the bundle has no risk thresholds.
    """
    if not inputs:
        return []
    thresholds = bundle.spec.risk_thresholds
    if thresholds is None:
        raise ModelNotAvailableError(f"model v{bundle.manifest.version} has no risk thresholds")
    features = build_features(schedule_frame(inputs), bundle.spec)
    p_late = bundle.calibrator.apply(predict_raw(bundle.booster, features))
    factors = top_factors(bundle.booster, features)
    return [
        Prediction(round(float(p), 4), risk_level(float(p), thresholds), f)
        for p, f in zip(p_late, factors, strict=True)
    ]


def _log_prediction(  # noqa: PLR0913 - one structured log line, all fields named
    *,
    event_id: str | None,
    eva: str,
    model_version: str,
    prediction: Prediction,
    latency_ms: float,
    request_id: str,
) -> None:
    get_logger("api").info(
        "prediction",
        extra={
            "event_id": event_id,
            "eva": eva,
            "model_version": model_version,
            "p_late": prediction.p_late,
            "risk_level": prediction.risk_level,
            "latency_ms": round(latency_ms, 2),
            "request_id": request_id,
        },
    )


def score_board(
    models: ChampionSource, rows: Sequence[BoardDeparture], *, request_id: str
) -> BoardScores:
    """Predictions for board rows: ``None`` for cancelled rows, and for every row when no
    champion is available (the board still shows live data, rules.md §6.3)."""
    started = time.perf_counter()
    active = [i for i, row in enumerate(rows) if not row.is_cancelled]
    try:
        bundle = models.get()
        scored = score(bundle, [ScheduleInput.from_board(rows[i]) for i in active])
    except ModelNotAvailableError:
        return BoardScores(None, [None] * len(rows))
    latency_ms = (time.perf_counter() - started) * 1000
    version = bundle.manifest.version
    predictions: list[Prediction | None] = [None] * len(rows)
    for i, prediction in zip(active, scored, strict=True):
        predictions[i] = prediction
        _log_prediction(
            event_id=rows[i].event_id,
            eva=rows[i].eva,
            model_version=version,
            prediction=prediction,
            latency_ms=latency_ms,
            request_id=request_id,
        )
    return BoardScores(version, predictions)


def predict_one(
    models: ChampionSource, item: ScheduleInput, *, request_id: str
) -> tuple[str, Prediction]:
    """Score one departure (`POST /predict`).

    Raises:
        ModelNotAvailableError: no champion is available.
    """
    started = time.perf_counter()
    bundle = models.get()
    [prediction] = score(bundle, [item])
    _log_prediction(
        event_id=None,
        eva=item.eva,
        model_version=bundle.manifest.version,
        prediction=prediction,
        latency_ms=(time.perf_counter() - started) * 1000,
        request_id=request_id,
    )
    return bundle.manifest.version, prediction
