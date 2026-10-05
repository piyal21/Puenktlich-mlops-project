"""Request and response models (mirrored in frontend/src/api/types.ts; keep in sync)."""

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from dbdelay.errors import ModelNotAvailableError
from dbdelay.features.calendar import BERLIN
from dbdelay.registry.artifacts import ModelBundle
from dbdelay.serving.board import BoardDeparture
from dbdelay.serving.scoring import Prediction

EVA_PATTERN = r"^[1-9][0-9]{6}$"
_BERLIN = ZoneInfo(BERLIN)
RiskLevelOut = Literal["low", "medium", "high"]
# Years outside this range are nonsense for a timetable model (and overflow pandas timestamps).
PREDICT_YEARS = (2000, 2099)


def berlin(value: datetime) -> datetime:
    """Display time zone of the API (architecture §7 examples use the Berlin offset)."""
    return value.astimezone(_BERLIN)


class Problem(BaseModel):
    """RFC 9457 problem details (`application/problem+json`)."""

    type: str
    title: str
    status: int
    detail: str
    instance: str
    request_id: str


class HealthOut(BaseModel):
    status: Literal["ok"]
    model_version: str | None
    board_generated_at: datetime | None


class StationOut(BaseModel):
    eva: str
    name: str
    state: str


class FactorOut(BaseModel):
    feature: str
    direction: Literal["up", "down"]
    text: str


class PredictionOut(BaseModel):
    p_late: float
    risk_level: RiskLevelOut
    top_factors: list[FactorOut]


class TrainOut(BaseModel):
    type: str
    number: str | None
    line: str | None
    destination: str | None


class DepartureOut(BaseModel):
    event_id: str
    planned_departure: datetime
    live_departure: datetime | None
    live_delay_min: int | None
    cancelled: bool
    platform: str | None
    train: TrainOut
    prediction: PredictionOut | None


class StationRef(BaseModel):
    eva: str
    name: str


class DeparturesOut(BaseModel):
    station: StationRef
    data_as_of: datetime
    stale: bool
    data_source: Literal["sample", "live"]
    replayed_from: date | None
    model_version: str | None
    departures: list[DepartureOut]


class PredictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eva: str = Field(pattern=EVA_PATTERN)
    train_type: str = Field(min_length=1, max_length=10, pattern=r"^[A-Za-z0-9]+$")
    train_number: str | None = Field(default=None, max_length=10)
    line_number: str | None = Field(default=None, max_length=10)
    final_destination: str | None = Field(default=None, max_length=100)
    stop_index: int = Field(ge=1, le=200)
    planned_departure: AwareDatetime

    @field_validator("planned_departure")
    @classmethod
    def _plausible_year(cls, value: datetime) -> datetime:
        first, last = PREDICT_YEARS
        if not first <= value.year <= last:
            raise ValueError(f"planned_departure must be between {first} and {last}")
        return value


class PredictOut(BaseModel):
    p_late: float
    risk_level: RiskLevelOut
    model_version: str
    top_factors: list[FactorOut]


class MetricsOut(BaseModel):
    test_brier: float | None
    test_auc: float | None
    test_pr_auc: float | None
    test_log_loss: float | None
    test_ece: float | None
    baseline_brier: float | None


class RiskThresholdsOut(BaseModel):
    medium: float
    high: float


class ModelOut(BaseModel):
    model_name: str
    version: str
    previous_version: str | None
    trained_at: datetime
    data_snapshot_id: str
    git_sha: str
    train_window: dict[str, date]
    metrics: MetricsOut
    risk_thresholds: RiskThresholdsOut


def prediction_out(prediction: Prediction) -> PredictionOut:
    return PredictionOut(
        p_late=prediction.p_late,
        risk_level=prediction.risk_level,
        top_factors=[
            FactorOut(feature=f.feature, direction=f.direction, text=f.text)
            for f in prediction.factors
        ],
    )


def departure_out(row: BoardDeparture, prediction: Prediction | None) -> DepartureOut:
    changed = row.changed_departure_utc
    return DepartureOut(
        event_id=row.event_id,
        planned_departure=berlin(row.planned_departure_utc),
        live_departure=None if changed is None else berlin(changed),
        live_delay_min=row.delay_min,
        cancelled=row.is_cancelled,
        platform=row.platform,
        train=TrainOut(
            type=row.train_type,
            number=row.train_number,
            line=row.line_number,
            destination=row.final_destination,
        ),
        prediction=None if prediction is None else prediction_out(prediction),
    )


def model_out(bundle: ModelBundle, previous_version: str | None) -> ModelOut:
    """Champion metadata for `/model` and the Health page.

    Raises:
        ModelNotAvailableError: if the bundle has no risk thresholds.
    """
    thresholds = bundle.spec.risk_thresholds
    if thresholds is None:
        raise ModelNotAvailableError(f"model v{bundle.manifest.version} has no risk thresholds")
    manifest, test = bundle.manifest, bundle.report.challenger_test.overall
    return ModelOut(
        model_name=manifest.model_name,
        version=manifest.version,
        previous_version=previous_version,
        trained_at=manifest.trained_at,
        data_snapshot_id=manifest.data_snapshot_id,
        git_sha=manifest.git_sha,
        train_window=manifest.train_window,
        metrics=MetricsOut(
            test_brier=test.brier,
            test_auc=test.roc_auc,
            test_pr_auc=test.pr_auc,
            test_log_loss=test.log_loss,
            test_ece=test.ece,
            baseline_brier=bundle.report.baseline_test.overall.brier,
        ),
        risk_thresholds=RiskThresholdsOut(medium=thresholds.medium, high=thresholds.high),
    )
