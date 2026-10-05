from typing import Annotated

from fastapi import APIRouter, Query

from app.deps import Deps
from app.schemas import StationOut
from dbdelay.serving.stations import search_stations

router = APIRouter(tags=["stations"])


@router.get("/stations", response_model=list[StationOut])
def list_stations(deps: Deps, q: Annotated[str, Query(max_length=50)] = "") -> list[StationOut]:
    """Supported stations matching ``q`` (all when empty)."""
    return [
        StationOut(eva=s.eva, name=s.name, state=s.state) for s in search_stations(deps.stations, q)
    ]
