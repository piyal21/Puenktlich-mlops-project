from datetime import UTC

from fastapi import APIRouter, Request

from app.deps import Deps
from app.errors import request_id_of
from app.schemas import PredictIn, PredictOut, prediction_out
from dbdelay.serving.scoring import ScheduleInput, predict_one

router = APIRouter(tags=["predict"])


@router.post("/predict", response_model=PredictOut)
def predict(body: PredictIn, request: Request, deps: Deps) -> PredictOut:
    """Score one departure described by its timetable fields (for developers)."""
    deps.station(body.eva)
    item = ScheduleInput(
        eva=body.eva,
        train_type=body.train_type.upper(),  # silver stores train types upper-case
        line_number=body.line_number,
        final_destination=body.final_destination,
        stop_index=body.stop_index,
        planned_departure_utc=body.planned_departure.astimezone(UTC),
    )
    version, prediction = predict_one(deps.models, item, request_id=request_id_of(request))
    out = prediction_out(prediction)
    return PredictOut(
        p_late=out.p_late,
        risk_level=out.risk_level,
        model_version=version,
        top_factors=out.top_factors,
    )
