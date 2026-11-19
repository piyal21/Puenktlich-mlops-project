from fastapi import APIRouter, Response

from app.deps import Deps
from dbdelay.serving.monitoring import MonitoringView

router = APIRouter(tags=["monitoring"])


@router.get("/monitoring", response_model=MonitoringView)
def monitoring(deps: Deps, response: Response) -> MonitoringView:
    """Daily monitoring results (newest day in full), the history and retrain decisions."""
    response.headers["Cache-Control"] = "public, max-age=300"
    return deps.monitoring.get()
