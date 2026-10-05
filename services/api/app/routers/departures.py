from typing import Annotated

from fastapi import APIRouter, Path, Query, Request

from app.deps import Deps
from app.errors import request_id_of
from app.schemas import EVA_PATTERN, DeparturesOut, StationRef, berlin, departure_out
from dbdelay.serving.board import is_stale, select_departures
from dbdelay.serving.scoring import score_board

router = APIRouter(tags=["departures"])


@router.get("/stations/{eva}/departures", response_model=DeparturesOut)
def station_departures(
    request: Request,
    deps: Deps,
    eva: Annotated[str, Path(pattern=EVA_PATTERN)],
    hours: Annotated[int, Query(ge=1, le=6)] = 3,
) -> DeparturesOut:
    """Live board of one station for the next ``hours`` with delay-risk forecasts."""
    station = deps.station(eva)
    board = deps.board.get()
    now = deps.clock()
    rows = select_departures(board, eva, now, hours)
    scores = score_board(deps.models, rows, request_id=request_id_of(request))
    return DeparturesOut(
        station=StationRef(eva=station.eva, name=station.name),
        data_as_of=berlin(board.generated_at),
        stale=is_stale(board, now, deps.settings.board_stale_after_s),
        data_source=board.source,
        replayed_from=board.replayed_from,
        model_version=scores.model_version,
        departures=[
            departure_out(row, prediction)
            for row, prediction in zip(rows, scores.predictions, strict=True)
        ],
    )
